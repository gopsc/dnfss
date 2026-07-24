// Command waf 是 micro_servers 的 WAF 入口网关。
//
// 部署形态：客户端 ──HTTPS──▶ waf:443 ──明文 HTTP──▶ gateway:内部端口
// WAF 终止 TLS、运行 coraza 规则匹配，未命中则转发到 gateway。
//
// 极客操作：
//
//	kill -HUP <pid>             # 热加载规则
//	waf -list                   # 列出所有规则
//	waf -test rules/local/x.conf # 校验单文件语法
//	waf -check "/?id=1' OR '1'='1" # 用当前规则测试输入
package main

import (
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"gopkg.in/yaml.v3"

	"github.com/micro_servers/waf/internal/engine"
	"github.com/micro_servers/waf/internal/proxy"
)

// Config WAF 配置文件结构。
type Config struct {
	Listen   string `yaml:"listen"`    // 监听地址，默认 :443
	TLSCert  string `yaml:"tls_cert"`  // 证书文件
	TLSKey   string `yaml:"tls_key"`   // 私钥文件
	Upstream string `yaml:"upstream"`  // gateway 地址，如 http://127.0.0.1:8443
	RulesDir string `yaml:"rules_dir"` // 规则根目录
}

func main() {
	var (
		configFile = flag.String("c", "config.yaml", "配置文件路径")
		doList     = flag.Bool("list", false, "列出所有规则后退出")
		doTest     = flag.String("test", "", "校验单个规则文件语法后退出")
		doCheck    = flag.String("check", "", "用当前规则测试输入字符串（URI/path）后退出")
	)
	flag.Parse()

	cfg := loadConfig(*configFile)

	// 子命令：极客 CLI 工具
	if *doTest != "" {
		if err := engine.TestFile(*doTest); err != nil {
			fmt.Fprintln(os.Stderr, "FAIL:", err)
			os.Exit(1)
		}
		fmt.Println("OK")
		return
	}

	eng, err := engine.New(&engine.Config{
		RulesDir:       cfg.RulesDir,
		ErrorCallback:  engine.LogError,
	})
	if err != nil {
		log.Fatalf("引擎初始化失败: %v", err)
	}

	if *doList {
		rules, err := eng.ListRules()
		if err != nil {
			log.Fatal(err)
		}
		out, _ := json.MarshalIndent(rules, "", "  ")
		fmt.Println(string(out))
		return
	}
	if *doCheck != "" {
		ids := eng.CheckInput(*doCheck)
		fmt.Printf("input=%q hit_rules=%v\n", *doCheck, ids)
		return
	}

	// 服务模式
	p, err := proxy.New(cfg.Upstream, eng)
	if err != nil {
		log.Fatalf("代理初始化失败: %v", err)
	}

	// SIGHUP 热加载
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGHUP)
	go func() {
		for range sigCh {
			log.Println("[reload] 收到 SIGHUP，重新加载规则")
			if err := eng.Reload(); err != nil {
				log.Printf("[reload] 失败，保留旧规则: %v", err)
			} else {
				log.Println("[reload] 成功")
			}
		}
	}()

	server := &http.Server{
		Addr:    cfg.Listen,
		Handler: p,
		TLSConfig: &tls.Config{
			MinVersion: tls.VersionTLS12,
		},
	}

	log.Printf("waf listening on %s, upstream=%s, rules=%s",
		cfg.Listen, cfg.Upstream, cfg.RulesDir)
	log.Printf("  - 热加载: kill -HUP %d", os.Getpid())
	log.Printf("  - 列规则: %s -c %s -list", os.Args[0], *configFile)

	if err := server.ListenAndServeTLS(cfg.TLSCert, cfg.TLSKey); err != nil {
		log.Fatal(err)
	}
}

func loadConfig(path string) *Config {
	data, err := os.ReadFile(path)
	if err != nil {
		log.Fatalf("读取配置失败: %v", err)
	}
	var c Config
	if err := yaml.Unmarshal(data, &c); err != nil {
		log.Fatalf("解析配置失败: %v", err)
	}
	if c.Listen == "" {
		c.Listen = ":443"
	}
	if c.RulesDir == "" {
		c.RulesDir = "./rules"
	}
	if c.Upstream == "" {
		log.Fatal("upstream 不能为空")
	}
	return &c
}
