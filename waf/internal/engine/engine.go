// Package engine 封装 coraza WAF，提供规则加载、热加载、规则列举与单条输入检测。
package engine

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/corazawaf/coraza/v3"
	"github.com/corazawaf/coraza/v3/types"

	"github.com/micro_servers/waf/internal/logclient"
)

// maxRuleFileSize 单个规则文件大小上限（1 MiB），防止超大文件导致 OOM
const maxRuleFileSize = 1 << 20

// checkRuleFileSize 校验单个规则文件大小是否超限，超限返回错误。
func checkRuleFileSize(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if info.Size() > maxRuleFileSize {
		return fmt.Errorf("规则文件过大: %s (%d bytes, 上限 %d)", path, info.Size(), maxRuleFileSize)
	}
	return nil
}

// Engine 持有当前生效的 WAF 实例，支持通过 Reload 热替换。
type Engine struct {
	mu  sync.RWMutex
	waf coraza.WAF
	cfg *Config
}

// Config 引擎配置。
type Config struct {
	// RulesDir 规则根目录，主入口文件为 <RulesDir>/waf.conf
	RulesDir string
	// ErrorCallback 规则加载/解析错误回调
	ErrorCallback func(mr types.MatchedRule)
}

// New 创建引擎并首次加载规则。
func New(cfg *Config) (*Engine, error) {
	e := &Engine{cfg: cfg}
	if err := e.Reload(); err != nil {
		return nil, err
	}
	return e, nil
}

// Reload 重新加载规则文件，原子替换内部 WAF 实例。
// 加载顺序：
//  1. <RulesDir>/waf.conf              基础配置
//  2. <RulesDir>/local/*.conf          本地自定义规则（按文件名字母序）
//  3. <RulesDir>/crs/coraza.conf       OWASP CRS 入口（若存在）
//  4. <RulesDir>/crs/rules/*.conf      OWASP CRS 规则（按文件名字母序）
//
// 不使用 coraza 的 Include 指令（其路径相对于 cwd 解析易出错），
// 改为 engine 自己扫描目录逐个 WithDirectivesFromFile 加载。
// 失败时保留旧实例不动，确保服务可用。
func (e *Engine) Reload() error {
	mainFile := filepath.Join(e.cfg.RulesDir, "waf.conf")
	if _, err := os.Stat(mainFile); err != nil {
		return fmt.Errorf("主规则文件不存在: %w", err)
	}
	if err := checkRuleFileSize(mainFile); err != nil {
		return err
	}

	builder := coraza.NewWAFConfig()
	if e.cfg.ErrorCallback != nil {
		builder = builder.WithErrorCallback(e.cfg.ErrorCallback)
	}

	// 1. 主配置
	builder = builder.WithDirectivesFromFile(mainFile)

	// 2. 本地规则（local/*.conf，按字母序）
	localFiles, _ := filepath.Glob(filepath.Join(e.cfg.RulesDir, "local", "*.conf"))
	for _, f := range localFiles {
		if err := checkRuleFileSize(f); err != nil {
			return err
		}
		builder = builder.WithDirectivesFromFile(f)
	}

	// 3. OWASP CRS（可选，crs/coraza.conf + crs/rules/*.conf）
	crsEntry := filepath.Join(e.cfg.RulesDir, "crs", "coraza.conf")
	if _, err := os.Stat(crsEntry); err == nil {
		if err := checkRuleFileSize(crsEntry); err != nil {
			return err
		}
		builder = builder.WithDirectivesFromFile(crsEntry)
		crsRuleFiles, _ := filepath.Glob(filepath.Join(e.cfg.RulesDir, "crs", "rules", "*.conf"))
		for _, f := range crsRuleFiles {
			if err := checkRuleFileSize(f); err != nil {
				return err
			}
			builder = builder.WithDirectivesFromFile(f)
		}
	}

	newWAF, err := coraza.NewWAF(builder)
	if err != nil {
		return fmt.Errorf("规则加载失败: %w", err)
	}

	e.mu.Lock()
	e.waf = newWAF
	e.mu.Unlock()
	return nil
}

// NewTransaction 创建一个事务，由调用方 Close。
func (e *Engine) NewTransaction() types.Transaction {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.waf.NewTransaction()
}

// CheckInput 用当前规则测试单个输入字符串，返回命中的规则 ID 列表。
// 极客 CLI 用：waf -check "/?id=1' UNION SELECT * from users"
// 输入按原始未编码字符串处理，内部对 query 做 URL 编码以符合 coraza ParseRequestURI 要求。
func (e *Engine) CheckInput(input string) []int {
	tx := e.NewTransaction()
	defer tx.Close()

	uri := input
	if !strings.HasPrefix(uri, "/") {
		uri = "/" + uri
	}

	// 用 net/url 解析并重新编码 query，正确处理无值 key、重复 key、特殊字符
	// 这样 coraza ProcessURI 内部的 url.ParseRequestURI 不会失败，ARGS 才能被正确提取
	if u, err := url.Parse(uri); err == nil && u.RawQuery != "" {
		u.RawQuery = u.Query().Encode()
		uri = u.String()
	}

	// 照搬 coraza/http/middleware.go 的 processRequest 流程
	tx.ProcessConnection("127.0.0.1", 8080, "127.0.0.1", 12345)
	tx.ProcessURI(uri, "GET", "HTTP/1.1")
	tx.AddRequestHeader("Host", "localhost")
	tx.SetServerName("localhost")
	if it := tx.ProcessRequestHeaders(); it != nil {
		return []int{it.RuleID}
	}
	if it, _ := tx.ProcessRequestBody(); it != nil {
		return []int{it.RuleID}
	}
	return nil
}

// ListRules 扫描规则目录，返回所有 SecRule 的 ID 与原始行。
// 仅做文本扫描，不解析语义——极客用 grep 友好。
// 处理 `\` 续行：以 `\` 结尾的行与下一行合并后再抽取 id。
func (e *Engine) ListRules() ([]Rule, error) {
	var rules []Rule
	err := filepath.Walk(e.cfg.RulesDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		if !strings.HasSuffix(path, ".conf") {
			return nil
		}
		// 超大规则文件跳过，不中断整个列举
		if fi, err := os.Stat(path); err == nil && fi.Size() > maxRuleFileSize {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(e.cfg.RulesDir, path)

		// 先合并续行，保留每条逻辑行在原文件的起始行号
		type logicalLine struct {
			startLine int
			text      string
		}
		var lines []logicalLine
		rawLines := strings.Split(string(data), "\n")
		for i := 0; i < len(rawLines); i++ {
			start := i + 1
			cur := strings.TrimRight(rawLines[i], "\r")
			// 合并以 \ 结尾的续行
			for strings.HasSuffix(cur, "\\") && i+1 < len(rawLines) {
				cur = cur[:len(cur)-1] + " " + strings.TrimRight(rawLines[i+1], "\r")
				i++
			}
			lines = append(lines, logicalLine{startLine: start, text: cur})
		}

		for _, ll := range lines {
			line := strings.TrimSpace(ll.text)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			if id, ok := extractRuleID(line); ok {
				rules = append(rules, Rule{
					ID:     id,
					File:   rel,
					Line:   ll.startLine,
					Source: line,
				})
			}
		}
		return nil
	})
	return rules, err
}

// Rule 描述一条 SecRule 的位置信息。
type Rule struct {
	ID     int    `json:"id"`
	File   string `json:"file"`
	Line   int    `json:"line"`
	Source string `json:"source"`
}

// extractRuleID 从一行 SecRule 中抠出 id:nnnn 字段。
// 形如: SecRule REQUEST_URI "@rx (?i)union.*select" "id:100001,phase:1,deny"
func extractRuleID(line string) (int, bool) {
	idx := strings.Index(line, "id:")
	if idx < 0 {
		return 0, false
	}
	rest := line[idx+3:]
	end := 0
	for end < len(rest) {
		c := rest[end]
		if c < '0' || c > '9' {
			break
		}
		end++
	}
	if end == 0 {
		return 0, false
	}
	var id int
	fmt.Sscanf(rest[:end], "%d", &id)
	return id, true
}

// TestFile 仅尝试加载单个规则文件，验证语法。
// 通过临时拼一个 Include 实现，目标文件转绝对路径以适配 coraza 解析目录。
func TestFile(file string) error {
	abs, err := filepath.Abs(file)
	if err != nil {
		return err
	}
	if _, err := os.Stat(abs); err != nil {
		return fmt.Errorf("文件不存在: %w", err)
	}
	if err := checkRuleFileSize(abs); err != nil {
		return err
	}

	tmp, err := os.CreateTemp("", "waf-test-*.conf")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	_, err = tmp.WriteString(fmt.Sprintf("Include %s\n", abs))
	tmp.Close()
	if err != nil {
		return err
	}

	builder := coraza.NewWAFConfig().WithDirectivesFromFile(tmp.Name())
	_, err = coraza.NewWAF(builder)
	if err != nil {
		return fmt.Errorf("语法错误: %w", err)
	}
	return nil
}

// LogError 默认错误回调，推送到日志库。
func LogError(mr types.MatchedRule) {
	logclient.Error("waf", fmt.Sprintf("coraza rule_id=%d severity=%s", mr.Rule().ID(), mr.Rule().Severity()),
		map[string]any{"rule_id": mr.Rule().ID(), "msg": mr.ErrorLog()})
}
