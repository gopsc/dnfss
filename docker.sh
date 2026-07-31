#!/bin/bash
#
# 微服务容器化管理脚本
# 用法: ./docker.sh [command]
#

set -e

cd "$(dirname "$0")"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

check_docker() {
    if ! command -v docker &> /dev/null; then
        error "Docker 未安装，请先安装 Docker Desktop"
    fi
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        error "Docker Compose 未安装"
    fi
}

# 使用 docker compose 或 docker-compose（兼容旧版本）
docker_compose() {
    if docker compose version &> /dev/null; then
        docker compose "$@"
    else
        docker-compose "$@"
    fi
}

cmd_build() {
    info "构建所有服务镜像..."
    docker_compose build --progress=plain "$@"
    info "构建完成"
}

cmd_up() {
    info "启动服务（后台运行）..."
    docker_compose up -d "$@"
    info "服务已启动"
    echo ""
    cmd_ps
}

cmd_down() {
    info "停止并移除容器..."
    docker_compose down "$@"
    info "服务已停止"
}

cmd_start() {
    info "启动已存在的容器..."
    docker_compose start "$@"
}

cmd_stop() {
    info "停止运行中的容器..."
    docker_compose stop "$@"
}

cmd_restart() {
    info "重启服务..."
    docker_compose restart "$@"
}

cmd_ps() {
    info "服务状态:"
    docker_compose ps
}

cmd_logs() {
    if [ -z "$1" ]; then
        docker_compose logs -f --tail=100
    else
        docker_compose logs -f --tail=100 "$@"
    fi
}

cmd_shell() {
    local service=${1:-dnfs}
    info "进入 $service 容器 shell..."
    docker_compose exec "$service" /bin/bash || docker_compose exec "$service" /bin/sh
}

cmd_rebuild() {
    info "重新构建并启动服务..."
    docker_compose build --no-cache "$@"
    docker_compose up -d "$@"
}

cmd_clean() {
    warn "此操作将停止所有容器并删除数据卷（数据库和ChromaDB数据将丢失！）"
    read -p "确认继续? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker_compose down -v
        info "已清理所有容器和数据卷"
    else
        info "已取消"
    fi
}

cmd_pull() {
    info "拉取基础镜像..."
    docker pull python:3.11-slim
}

cmd_health() {
    info "检查服务健康状态..."
    local services=("db:8005" "ai_review:8007" "dnfs:8006")
    for svc in "${services[@]}"; do
        IFS=':' read -r name port <<< "$svc"
        if nc -z 127.0.0.1 "$port" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} $name :$port  健康"
        else
            echo -e "  ${RED}✗${NC} $name :$port  不可达"
        fi
    done
}

# ── 数据库管理命令 ──────────────────────────────────────────────

DB_CONTAINER="micro-db"

_check_db_running() {
    if ! docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
        error "db 容器未运行，请先启动服务: ./docker.sh up"
    fi
}

cmd_db_info() {
    _check_db_running
    info "数据库信息:"
    docker_compose exec db python db_tools.py info
}

cmd_db_backup() {
    _check_db_running
    local backup_file="${1:-}"
    if [ -z "$backup_file" ]; then
        backup_file="backup_$(date +%Y%m%d_%H%M%S).db"
    fi
    info "执行数据库备份..."
    # 先做 WAL checkpoint 确保数据完整
    docker_compose exec db python db_tools.py wal-checkpoint
    # 备份到容器内的 /app/backups 目录（映射到宿主机 ./backups）
    docker_compose exec db python db_tools.py backup "/app/backups/$backup_file"
    echo ""
    info "备份文件: ./backups/$backup_file"
    ls -lh "./backups/$backup_file" 2>/dev/null || info "(文件已写入容器 /app/backups/) "
}

cmd_db_export() {
    _check_db_running
    local export_file="${1:-}"
    if [ -z "$export_file" ]; then
        export_file="export_$(date +%Y%m%d_%H%M%S).json"
    fi
    info "导出数据库为 JSON..."
    docker_compose exec db python db_tools.py export "/app/backups/$export_file"
    echo ""
    info "导出文件: ./backups/$export_file"
}

cmd_db_import() {
    local import_file="$1"
    if [ -z "$import_file" ]; then
        error "用法: ./docker.sh db-import <json文件路径>"
    fi
    if [ ! -f "./backups/$import_file" ] && [ ! -f "$import_file" ]; then
        # 检查是否在backups目录
        error "导入文件不存在: $import_file (请放在 ./backups/ 目录下或提供绝对路径)"
    fi
    _check_db_running
    
    local container_path="/app/backups/$(basename "$import_file")"
    # 如果是宿主机路径且不在backups下，先复制到backups
    if [ -f "$import_file" ] && [ ! -f "./backups/$(basename "$import_file")" ]; then
        warn "复制文件到 backups 目录..."
        cp "$import_file" "./backups/"
    fi
    
    warn "即将导入数据，现有数据将被覆盖！"
    read -p "确认继续? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # 先备份当前数据
        cmd_db_backup "before_import_$(date +%Y%m%d_%H%M%S).db"
        info "开始导入..."
        docker_compose exec db python db_tools.py import "$container_path"
    else
        info "已取消"
    fi
}

cmd_db_vacuum() {
    _check_db_running
    info "执行 VACUUM 压缩数据库..."
    docker_compose exec db python db_tools.py vacuum
}

cmd_db_restore() {
    local backup_file="$1"
    if [ -z "$backup_file" ]; then
        error "用法: ./docker.sh db-restore <备份文件路径> (从 ./backups/ 目录)"
        error "示例: ./docker.sh db-restore backup_20260730_120000.db"
    fi
    
    local backup_path="./backups/$backup_file"
    if [ ! -f "$backup_path" ]; then
        # 尝试绝对路径
        if [ -f "$backup_file" ]; then
            backup_path="$backup_file"
        else
            error "备份文件不存在: $backup_file"
        fi
    fi
    
    _check_db_running
    
    warn "即将从备份恢复数据库，当前数据将被覆盖！"
    warn "备份文件: $backup_path"
    read -p "确认继续? [y/N] 输入 YES 确认: " -r
    echo
    if [[ "$REPLY" == "YES" ]]; then
        info "停止 dnfs 和 ai_review 服务..."
        docker_compose stop dnfs ai_review
        
        info "复制备份文件到容器..."
        docker cp "$backup_path" "$DB_CONTAINER:/app/data/database.db"
        
        info "重启 db 服务..."
        docker_compose restart db
        
        # 等待db健康
        info "等待 db 服务恢复..."
        sleep 5
        
        info "启动其他服务..."
        docker_compose start dnfs ai_review
        
        info "恢复完成！"
    else
        info "已取消"
    fi
}

cmd_db_list_backups() {
    info "备份文件列表 (./backups/):"
    if [ -d "./backups" ]; then
        ls -lhS ./backups/ 2>/dev/null | grep -v "total" | grep -v ".gitkeep" || echo "  (无备份文件)"
    else
        echo "  backups 目录不存在"
    fi
}

usage() {
    echo "微服务容器化管理脚本"
    echo ""
    echo "用法: ./docker.sh <command> [options]"
    echo ""
    echo "── 服务管理 ──"
    echo "  build       构建所有服务镜像"
    echo "  up          启动所有服务（后台运行）"
    echo "  down        停止并移除所有容器"
    echo "  start       启动已停止的容器"
    echo "  stop        停止运行中的容器"
    echo "  restart     重启服务"
    echo "  ps          查看服务状态"
    echo "  logs [svc]  查看日志（默认所有服务，可指定服务名）"
    echo "  shell [svc] 进入容器shell（默认dnfs）"
    echo "  rebuild     重新构建（无缓存）并启动"
    echo "  health      检查服务健康状态"
    echo "  pull        拉取最新基础镜像"
    echo "  clean       停止并删除所有容器+数据卷（危险！）"
    echo ""
    echo "── 数据库管理 (SQLite) ──"
    echo "  db-info     查看数据库信息和表统计"
    echo "  db-backup [file]  备份数据库（.db 二进制文件，输出到 ./backups/）"
    echo "  db-export [file]  导出数据为 JSON（跨平台传输，输出到 ./backups/）"
    echo "  db-import <file>  从 JSON 导入数据（文件放在 ./backups/ 下）"
    echo "  db-restore <file> 从 .db 备份文件恢复"
    echo "  db-vacuum         VACUUM 压缩数据库"
    echo "  db-ls             列出备份文件"
    echo ""
    echo "示例:"
    echo "  ./docker.sh build dnfs       # 仅构建dnfs服务"
    echo "  ./docker.sh logs dnfs        # 查看dnfs日志"
    echo "  ./docker.sh shell db         # 进入db容器"
    echo "  ./docker.sh db-backup        # 立即备份"
    echo "  ./docker.sh db-export        # 导出JSON用于迁移"
    echo "  ./docker.sh db-ls            # 查看现有备份"
    echo ""
}

# 主逻辑
check_docker

case "${1:-}" in
    build)
        shift
        cmd_build "$@"
        ;;
    up)
        shift
        cmd_up "$@"
        ;;
    down)
        shift
        cmd_down "$@"
        ;;
    start)
        shift
        cmd_start "$@"
        ;;
    stop)
        shift
        cmd_stop "$@"
        ;;
    restart)
        shift
        cmd_restart "$@"
        ;;
    ps|status)
        cmd_ps
        ;;
    logs)
        shift
        cmd_logs "$@"
        ;;
    shell)
        shift
        cmd_shell "$@"
        ;;
    rebuild)
        shift
        cmd_rebuild "$@"
        ;;
    health)
        cmd_health
        ;;
    pull)
        cmd_pull
        ;;
    clean)
        cmd_clean
        ;;
    # ── 数据库管理命令 ──
    db-info|dbinfo)
        cmd_db_info
        ;;
    db-backup|dbbackup)
        shift
        cmd_db_backup "$@"
        ;;
    db-export|dbexport)
        shift
        cmd_db_export "$@"
        ;;
    db-import|dbimport)
        shift
        cmd_db_import "$@"
        ;;
    db-restore|dbrestore)
        shift
        cmd_db_restore "$@"
        ;;
    db-vacuum|dbvacuum)
        cmd_db_vacuum
        ;;
    db-ls|dbls)
        cmd_db_list_backups
        ;;
    *)
        usage
        exit 1
        ;;
esac
