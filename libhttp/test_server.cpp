#include "us/HttpServer.hpp"
#include <iostream>
#include <thread>
#include <chrono>
#include <signal.h>

using namespace pmc::net;

static std::unique_ptr<HttpServer> g_server;

void signalHandler(int signum) {
    std::cout << "\nStopping server..." << std::endl;
    if (g_server) g_server->stop();
}

int main() {
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    try {
        // 创建 HTTP 服务器（同时支持 HTTP 和 WebSocket）
        g_server = std::make_unique<HttpServer>("0.0.0.0", 9090, 2);

        // 注册普通 HTTP GET 接口
        g_server->get("/hello", [](const auto& req, const auto& params) {
            http::response<http::string_body> res;
            res.version(req.version());
            res.set(http::field::content_type, "text/plain; charset=utf-8");
            res.result(http::status::ok);
            res.body() = "Hello from HTTP! Server is working correctly.";
            res.prepare_payload();
            return res;
        });

        // 注册 HTTP POST 接口
        g_server->post("/echo", [](const auto& req, const auto& params) {
            http::response<http::string_body> res;
            res.version(req.version());
            res.set(http::field::content_type, "application/json; charset=utf-8");
            res.result(http::status::ok);
            res.body() = std::string("{\"received\":\"") + req.body() + "\"}";
            res.prepare_payload();
            return res;
        });

        // WebSocket 测试页面
        g_server->get("/test", [](const auto& req, const auto& params) {
            http::response<http::string_body> res;
            res.version(req.version());
            res.set(http::field::content_type, "text/html; charset=utf-8");
            res.result(http::status::ok);
            res.body() = R"HTML(<!DOCTYPE html>
<html><head><title>WebSocket Test</title>
<style>body{font-family:monospace;padding:20px}#log{border:1px solid #ccc;padding:10px;height:300px;overflow-y:scroll}</style>
</head><body>
<h2>HTTP+WebSocket Integration Test</h2>
<p>HTTP: <a href="/hello">/hello</a> | <a href="#" onclick="testPost();return false">POST /echo</a></p>
<button onclick="testWs('/ws')">Connect /ws (echo)</button>
<button onclick="testWs('/chat')">Connect /chat</button>
<button onclick="clearLog()">Clear</button>
<div id="log"></div>
<script>
const logDiv = document.getElementById('log');
function log(msg, color) {
  color = color || 'black';
  logDiv.innerHTML += '<span style="color:'+color+'">'+new Date().toLocaleTimeString()+' - '+msg+'</span><br>';
  logDiv.scrollTop = logDiv.scrollHeight;
}
function clearLog() { logDiv.innerHTML = ''; }

function testPost() {
  fetch('/echo',{method:'POST',body:'Hello POST'})
    .then(function(r){return r.text()}).then(function(t){log('POST /echo: '+t,'blue')});
}

function testWs(path) {
  log('Connecting to ws://'+location.host+path+'...');
  var ws = new WebSocket('ws://'+location.host+path);
  ws.onopen = function() { log(path+' connected','green'); ws.send('Hello from browser to '+path); };
  ws.onmessage = function(e) { log(path+' received: '+e.data,'blue'); };
  ws.onerror = function(e) { log(path+' error','red'); };
  ws.onclose = function() { log(path+' closed','gray'); };
}
log('Page loaded. HTTP server working. Use buttons above to test WebSocket.');
</script></body></html>)HTML";
            res.prepare_payload();
            return res;
        });

        // 注册 WebSocket 接口 - Echo 服务器
        g_server->ws("/ws", [](const std::string& clientId, const std::string& message,
                               std::function<void(const std::string&)> send) {
            std::cout << "[WS] Received from " << clientId << ": " << message << std::endl;
            // Echo back with prefix
            send(std::string("Echo: ") + message);
        });

        // WebSocket 另一个接口 - Chat
        g_server->ws("/chat", [](const std::string& clientId, const std::string& message,
                                 std::function<void(const std::string&)> send) {
            std::cout << "[Chat] Received from " << clientId << ": " << message << std::endl;
            send(std::string("[Chat] ") + message);
        });

        // WebSocket 连接回调
        g_server->onWsConnect([](const std::string& clientId, const std::string& address) {
            std::cout << "[WS] Client connected: " << clientId << " from " << address << std::endl;
        });

        // WebSocket 断开回调
        g_server->onWsDisconnect([](const std::string& clientId) {
            std::cout << "[WS] Client disconnected: " << clientId << std::endl;
        });

        // 启动服务器
        g_server->showStatus();
        g_server->start();

        std::cout << "\n=== Test Server Running ===" << std::endl;
        std::cout << "HTTP test: curl http://localhost:9090/hello" << std::endl;
        std::cout << "WebSocket test: ws://localhost:9090/ws (echo) or ws://localhost:9090/chat (chat)" << std::endl;
        std::cout << "Press Ctrl+C to stop" << std::endl;
        std::cout << std::endl;

        // 3秒后广播一条消息给所有 WebSocket 客户端
        std::thread broadcast_thread([&]() {
            std::this_thread::sleep_for(std::chrono::seconds(3));
            if (g_server && g_server->getWsClientCount() > 0) {
                std::cout << "Broadcasting to all clients..." << std::endl;
                g_server->broadcastWs("Server broadcast after 3 seconds!");
            }
        });
        broadcast_thread.detach();

        // 运行事件循环
        g_server->run();

    } catch (const std::exception& e) {
        std::cerr << "Server error: " << e.what() << std::endl;
        return 1;
    }

    std::cout << "Server stopped." << std::endl;
    return 0;
}
