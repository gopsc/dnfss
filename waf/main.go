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
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/micro_servers/waf/internal/engine"
	"github.com/micro_servers/waf/internal/logclient"
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
		configFile  = flag.String("c", "config.yaml", "配置文件路径")
		doList      = flag.Bool("list", false, "列出所有规则后退出")
		doTest      = flag.String("test", "", "校验单个规则文件语法后退出")
		doCheck     = flag.String("check", "", "用当前规则测试输入字符串（URI/path）后退出")
		// CLI 标准化参数（覆盖 config.yaml）
		cliListen   = flag.String("listen", "", "监听地址（别名: --host），覆盖 config.yaml")
		cliHost     = flag.String("host", "", "监听地址（--listen 别名）")
		cliUpstream = flag.String("upstream", "", "gateway 地址，覆盖 config.yaml")
		cliTLSCert  = flag.String("tls-cert", "", "TLS 证书文件，覆盖 config.yaml")
		cliTLSKey   = flag.String("tls-key", "", "TLS 私钥文件，覆盖 config.yaml")
		cliRulesDir = flag.String("rules-dir", "", "规则根目录，覆盖 config.yaml")
		logLevel    = flag.String("log-level", "info", "日志级别: debug|info|warn|error")
		instance    = flag.String("instance", "waf", "实例名（注册中心用）")
	)
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr,
			"waf — WAF 入口网关\n\n"+
				"用法: waf [选项]\n\n"+
				"选项:\n"+
				"  -c <file>         配置文件路径，默认 config.yaml\n"+
				"  --listen <addr>   监听地址（别名: --host），覆盖 config.yaml\n"+
				"  --upstream <url>  gateway 地址，覆盖 config.yaml\n"+
				"  --tls-cert <file> TLS 证书文件，覆盖 config.yaml\n"+
				"  --tls-key <file>  TLS 私钥文件，覆盖 config.yaml\n"+
				"  --rules-dir <dir> 规则根目录，覆盖 config.yaml\n"+
				"  --log-level <lv>  debug|info|warn|error，默认 info\n"+
				"  --instance <name> 实例名，默认 waf\n"+
				"  -list             列出所有规则后退出\n"+
				"  -test <file>      校验单个规则文件语法后退出\n"+
				"  -check <input>    用当前规则测试输入字符串后退出\n"+
				"  -h, --help        显示此帮助并退出\n\n"+
				"配置优先级: CLI > config.yaml\n")
	}
	flag.Parse()

	// 日志级别
	logclient.SetLevel(*logLevel)

	cfg := loadConfig(*configFile)

	// CLI 覆盖 config.yaml
	if *cliListen != "" {
		cfg.Listen = *cliListen
	}
	if *cliHost != "" {
		cfg.Listen = *cliHost
	}
	if *cliUpstream != "" {
		cfg.Upstream = *cliUpstream
	}
	if *cliTLSCert != "" {
		cfg.TLSCert = *cliTLSCert
	}
	if *cliTLSKey != "" {
		cfg.TLSKey = *cliTLSKey
	}
	if *cliRulesDir != "" {
		cfg.RulesDir = *cliRulesDir
	}

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
		RulesDir:      cfg.RulesDir,
		ErrorCallback: engine.LogError,
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
	logclient.Init()
	// 标准日志输出到 stderr（systemd/journalctl 可捕获），保留 net/http 的 panic 堆栈与 TLS 错误
	log.SetOutput(os.Stderr)

	p, err := proxy.New(cfg.Upstream, eng)
	if err != nil {
		logclient.Error("waf", fmt.Sprintf("代理初始化失败: %v", err), nil)
		logclient.Flush()
		os.Exit(1)
	}

	// SIGHUP 热加载
	hupCh := make(chan os.Signal, 1)
	signal.Notify(hupCh, syscall.SIGHUP)
	go func() {
		for range hupCh {
			if err := eng.Reload(); err != nil {
				logclient.Error("waf", fmt.Sprintf("热加载失败，保留旧规则: %v", err), nil)
			} else {
				logclient.Info("waf", "规则热加载成功", nil)
			}
		}
	}()

	server := &http.Server{
		Addr:              cfg.Listen,
		Handler:           p,
		ReadTimeout:       15 * time.Second,
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
		MaxHeaderBytes:    1 << 20, // 1MB
		TLSConfig: &tls.Config{
			MinVersion: tls.VersionTLS12,
			// 显式限定安全 CipherSuites，排除弱加密套件（RC4/3DES/CBC 等）
			CipherSuites: []uint16{
				// TLS 1.3 套件（Go 自动选择，显式列出便于审计）
				tls.TLS_AES_128_GCM_SHA256,
				tls.TLS_AES_256_GCM_SHA384,
				tls.TLS_CHACHA20_POLY1305_SHA256,
				// TLS 1.2 ECDHE 套件（前向保密 + AEAD）
				tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
				tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
				tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
				tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
				tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
				tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
			},
			// 优先使用高性能且安全的椭圆曲线
			CurvePreferences: []tls.CurveID{
				tls.X25519,
				tls.CurveP256,
				tls.CurveP384,
			},
		},
	}

	// SIGINT/SIGTERM 优雅关闭：等待进行中的请求完成后再退出，并 flush 队列日志
	termCh := make(chan os.Signal, 1)
	signal.Notify(termCh, syscall.SIGINT, syscall.SIGTERM)

	logclient.Info("waf", fmt.Sprintf("服务启动 instance=%s listen=%s upstream=%s rules=%s pid=%d",
		*instance, cfg.Listen, cfg.Upstream, cfg.RulesDir, os.Getpid()), nil)

	errCh := make(chan error, 2)
	// 同一端口区分 TLS 与明文 HTTP：明文请求 301 重定向到 HTTPS（原 redirect80 的职责）
	cert, err := tls.LoadX509KeyPair(cfg.TLSCert, cfg.TLSKey)
	if err != nil {
		logclient.Error("waf", fmt.Sprintf("加载 TLS 证书失败: %v", err), nil)
		logclient.Flush()
		os.Exit(1)
	}
	server.TLSConfig.Certificates = []tls.Certificate{cert}
	rawLn, err := net.Listen("tcp", cfg.Listen)
	if err != nil {
		logclient.Error("waf", fmt.Sprintf("监听失败: %v", err), nil)
		logclient.Flush()
		os.Exit(1)
	}
	tlsCh := make(chan net.Conn)
	plainCh := make(chan net.Conn)
	go acceptAndDispatch(rawLn, tlsCh, plainCh)

	plainServer := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "https://"+r.Host+r.URL.RequestURI(), http.StatusMovedPermanently)
		}),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		errCh <- server.Serve(tls.NewListener(&chanListener{ch: tlsCh, addr: rawLn.Addr()}, server.TLSConfig))
	}()
	go func() {
		errCh <- plainServer.Serve(&chanListener{ch: plainCh, addr: rawLn.Addr()})
	}()

	select {
	case err := <-errCh:
		if err != nil && err != http.ErrServerClosed {
			logclient.Error("waf", fmt.Sprintf("TLS 监听失败: %v", err), nil)
			logclient.Flush()
			os.Exit(1)
		}
	case <-termCh:
		logclient.Info("waf", "收到终止信号，开始优雅关闭", nil)
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(ctx); err != nil {
			logclient.Error("waf", fmt.Sprintf("优雅关闭超时: %v", err), nil)
		}
		plainServer.Shutdown(ctx)
		logclient.Flush()
	}
}

// bufferedConn 保留已 peek 的首字节，避免后续 Read 丢数据。
type bufferedConn struct {
	net.Conn
	r *bufio.Reader
}

func (c *bufferedConn) Read(p []byte) (int, error) { return c.r.Read(p) }

// chanListener 从 channel 取连接，供两个 http.Server 分别消费 TLS / 明文连接。
type chanListener struct {
	ch   chan net.Conn
	addr net.Addr
}

func (l *chanListener) Accept() (net.Conn, error) {
	c, ok := <-l.ch
	if !ok {
		return nil, io.EOF
	}
	return c, nil
}

func (l *chanListener) Close() error   { return nil }
func (l *chanListener) Addr() net.Addr { return l.addr }

// acceptAndDispatch 按首字节分发：0x16 为 TLS ClientHello → TLS 通道，其余 → 明文 HTTP 通道。
func acceptAndDispatch(ln net.Listener, tlsCh, plainCh chan<- net.Conn) {
	for {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		br := bufio.NewReader(c)
		b, err := br.Peek(1)
		if err == nil && len(b) == 1 && b[0] == 0x16 {
			tlsCh <- &bufferedConn{Conn: c, r: br}
		} else {
			plainCh <- &bufferedConn{Conn: c, r: br}
		}
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
