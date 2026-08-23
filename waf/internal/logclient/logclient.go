// Package logclient 基于 gomsg 的集中式日志客户端（异步批量推送 db2 Logs 表）。
//
// 原实现（pymsg 帧协议 + 异步队列）已抽取至 ../gomsg，本包保留包级 API
// 兼容层：main / engine / proxy 的调用点零改动。
package logclient

import "github.com/micro_servers/gomsg"

var (
	lc           *gomsg.LogClient
	pendingLevel string
)

// Init 初始化全局日志客户端（读 LOG_DB_ADDR / LOG_LEVEL 环境变量）。
func Init() {
	lc = gomsg.NewLogClient("")
	if pendingLevel != "" {
		lc.SetLevel(pendingLevel)
	}
}

// SetLevel 设置最低日志级别（debug|info|warn|error，大小写不敏感）。
func SetLevel(level string) {
	pendingLevel = level
	if lc != nil {
		lc.SetLevel(level)
	}
}

// Push 推送一条日志（非阻塞，队列满或低于最低级别时静默丢弃）。
func Push(service, level, message string, extra map[string]any) {
	if lc == nil {
		return
	}
	lc.Push(gomsg.Entry{
		Service: service,
		Level:   level,
		Message: message,
		Extra:   extra,
	})
}

// Debug 推送 DEBUG 级别日志。
func Debug(service, message string, extra map[string]any) {
	Push(service, "DEBUG", message, extra)
}

// Info 推送 INFO 级别日志。
func Info(service, message string, extra map[string]any) {
	Push(service, "INFO", message, extra)
}

// Warning 推送 WARNING 级别日志。
func Warning(service, message string, extra map[string]any) {
	Push(service, "WARNING", message, extra)
}

// Error 推送 ERROR 级别日志。
func Error(service, message string, extra map[string]any) {
	Push(service, "ERROR", message, extra)
}

// Flush 停止队列并 flush 剩余日志（用于进程退出前）。
func Flush() {
	if lc != nil {
		lc.Flush()
	}
}
