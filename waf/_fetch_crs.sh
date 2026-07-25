#!/bin/bash
# 拉取 OWASP CRS 规则（通过 Go module proxy，不依赖 GitHub 直连）
#
# 用法：
#   bash _fetch_crs.sh          # 拉取最新版
#   bash _fetch_crs.sh v4.25.0  # 拉取指定版本
#
# 规则来源：github.com/corazawaf/coraza-coreruleset/v4（Apache-2.0）
# 规则文件放置在 rules/crs/rules/，入口配置 rules/crs/coraza.conf
# 整个 rules/crs/ 目录已在 .gitignore 中，不入库。
set -euo pipefail
cd "$(dirname "$0")" || exit

VERSION="${1:-latest}"
CRS_DIR="rules/crs"
RULES_DIR="$CRS_DIR/rules"

echo "==== 拉取 OWASP CRS (${VERSION}) ===="

# 1. 通过 Go module proxy 下载 coraza-coreruleset
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
WORKDIR="$TMPDIR/crs-fetch"
mkdir -p "$WORKDIR"

cd "$WORKDIR"
cat > go.mod << 'EOF'
module crs-fetch
go 1.22.0
EOF

echo "[1/4] 下载 coraza-coreruleset (${VERSION}) ..."
if [ "$VERSION" = "latest" ]; then
    GOPROXY=https://goproxy.cn,direct go mod download github.com/corazawaf/coraza-coreruleset/v4@latest 2>&1
else
    GOPROXY=https://goproxy.cn,direct go mod download "github.com/corazawaf/coraza-coreruleset/v4@${VERSION}" 2>&1
fi

# 2. 定位模块缓存
GOMODCACHE=$(go env GOMODCACHE)
# 找到实际下载的版本目录（可能是 v4@v4.25.0 等形式）
MOD_DIR=$(ls -d "$GOMODCACHE"/github.com/corazawaf/coraza-coreruleset/v4@* 2>/dev/null | tail -1)
if [ -z "$MOD_DIR" ]; then
    echo "[ERROR] 未在模块缓存中找到 coraza-coreruleset" >&2
    exit 1
fi
ACTUAL_VER=$(basename "$MOD_DIR" | sed 's/v4@//')
echo "  实际版本: ${ACTUAL_VER}"

SRC_RULES="$MOD_DIR/rules/@owasp_crs"
if [ ! -d "$SRC_RULES" ]; then
    echo "[ERROR] 规则目录不存在: $SRC_RULES" >&2
    exit 1
fi

cd - > /dev/null

# 3. 复制规则文件
echo "[2/4] 复制规则文件到 ${RULES_DIR}/ ..."
mkdir -p "$RULES_DIR"
cp "$SRC_RULES"/*.conf "$SRC_RULES"/*.data "$RULES_DIR/"
CONF_COUNT=$(ls "$RULES_DIR"/*.conf 2>/dev/null | wc -l | tr -d ' ')
DATA_COUNT=$(ls "$RULES_DIR"/*.data 2>/dev/null | wc -l | tr -d ' ')
echo "  ${CONF_COUNT} 个规则文件, ${DATA_COUNT} 个数据文件"

# 4. 复制 LICENSE
echo "[3/4] 复制 LICENSE ..."
cp "$MOD_DIR/rules/LICENSE" "$CRS_DIR/LICENSE" 2>/dev/null || true

# 5. 生成入口配置 crs/coraza.conf
echo "[4/4] 生成 ${CRS_DIR}/coraza.conf ..."
# 从版本号算出 crs_setup_version：v4.25.0 → 4250
SETUP_VER=$(echo "$ACTUAL_VER" | sed 's/^v//; s/\.//g; s/\([0-9]*\).*/\1/' | head -c 4)
# 更精确：取 major*1000 + minor*10 + patch
MAJOR=$(echo "$ACTUAL_VER" | sed 's/^v//; s/\..*//')
MINOR=$(echo "$ACTUAL_VER" | sed 's/^v[0-9]*\.//; s/\..*//')
PATCH=$(echo "$ACTUAL_VER" | sed 's/^v[0-9]*\.[0-9]*\.//; s/[^0-9].*//')
SETUP_VER="${MAJOR}${MINOR}${PATCH}"

cat > "$CRS_DIR/coraza.conf" << CONFEOF
# crs/coraza.conf —— OWASP CRS 入口配置（由 _fetch_crs.sh 自动生成）
#
# 加载顺序（由 engine.Reload 控制）：
#   1. rules/waf.conf          基础配置（SecRuleEngine On 等）
#   2. rules/local/*.conf      本地自定义规则
#   3. rules/crs/coraza.conf   ← 本文件（CRS 初始化）
#   4. rules/crs/rules/*.conf  CRS 规则文件（按文件名字母序）
#
# 本文件职责：
#   - 设置 CRS 必需的事务变量（crs_setup_version）
#   - 初始化 XML/JSON 请求体处理器
#
# 不在此文件设置的项（由 waf.conf 统一管理）：
#   - SecRuleEngine / SecRequestBodyAccess / SecResponseBodyAccess
#   - SecRequestBodyLimit
#   - SecDefaultAction（coraza 不允许同 phase 重复定义）

# ===== 请求体处理器初始化 =====
SecRule REQUEST_HEADERS:Content-Type "^(?:application(?:/soap\+|/)|text/)xml" \\
     "id:'200000',phase:1,t:none,t:lowercase,pass,nolog,ctl:requestBodyProcessor=XML"

SecRule REQUEST_HEADERS:Content-Type "^application/json" \\
     "id:'200001',phase:1,t:none,t:lowercase,pass,nolog,ctl:requestBodyProcessor=JSON"

SecRule REQUEST_HEADERS:Content-Type "^application/[a-z0-9.-]+[+]json" \\
     "id:'200006',phase:1,t:none,t:lowercase,pass,nolog,ctl:requestBodyProcessor=JSON"

# ===== CRS 必需变量 =====
# 规则 901001 检查此变量，未设置则返回 500
SecAction \\
    "id:900990,\\
    phase:1,\\
    pass,\\
    nolog,\\
    setvar:tx.crs_setup_version=${SETUP_VER}"

# ===== 可选调优（取消注释以启用） =====
# 异常评分阈值（默认 inbound=5, outbound=4）
# SecAction \\
#     "id:900110,\\
#     phase:1,\\
#     pass,\\
#     nolog,\\
#     setvar:tx.inbound_anomaly_score_threshold=5,\\
#     setvar:tx.outbound_anomaly_score_threshold=4"

# 偏执等级（默认 1；等级越高规则越严、误报越多）
# SecAction \\
#     "id:900000,\\
#     phase:1,\\
#     pass,\\
#     nolog,\\
#     setvar:tx.blocking_paranoia_level=1"
CONFEOF

echo ""
echo "==== 完成 ===="
echo "  版本: OWASP CRS ${ACTUAL_VER}"
echo "  规则: ${RULES_DIR}/ (${CONF_COUNT} conf + ${DATA_COUNT} data)"
echo "  入口: ${CRS_DIR}/coraza.conf"
echo "  许可: Apache-2.0 (${CRS_DIR}/LICENSE)"
echo ""
echo "验证: ./waf.bin -c config.yaml -list | head"
echo "热加载: kill -HUP \$(pidof waf.bin)"
