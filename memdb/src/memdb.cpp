#include <us/RpcServer.hpp>
#include <us/RpcClient.hpp>

#include <boost/json.hpp>

#include <us/memdb.hpp>

#include <string>
#include <iostream>

namespace json = boost::json;



struct ClientConfig {
    std::string connected_host      = "127.0.0.1";
    int         connected_port      = 8005;
    std::string service_name        = "db2";
    double      timeout             = 10.0;
    bool        retry_on_refused    = true;
};

struct ServerConfig {
    std::string listen_host     = "127.0.0.1";
    int         listen_port     = 8011;
    int         workers         = 4;
    std::string db_url          = "sqlite:///database.db";
};

static std::string get_db_path(const std::string url) {
    const std::string prefix = "sqlite:///";
    if (url.rfind(prefix, 0) == 0) {
        return url.substr(prefix.size());
    }
    return url;
}



class DBConnection {
private:
    ClientConfig __cfg;


public:
    explicit DBConnection(const ClientConfig& cfg) :
        __cfg(cfg) {}
    ~DBConnection() = default;

    std::string get_from_sql(const std::string& sql_name) {
        pmc::net::RpcClient client(__cfg.connected_host, __cfg.connected_port, __cfg.service_name, __cfg.timeout, __cfg.retry_on_refused);

        try {
            auto data = client.call("execute", {{"action", "select"}, {"table", "memdb"}, {"where", {"sql_name", sql_name}}});
            
        } catch (const std::exception& err) {
            std::cerr << "[memdb] Error: 连接失败: " << err.what() << std::endl;
            std::exit(1);
        }
    }
};


class MemdbServer {


private:
    ServerConfig __cfg;


public:
    explicit MemdbServer(const ServerConfig& cfg) :
        __cfg(cfg) {}

    void start() {
        //TODO 编写
    }
};