/** db2 — C++20 数据库 RPC 服务（对齐 Python db 模块的 execute 路由）
 *
 * 协议约定（与 Python/Go 客户端字节级兼容）：
 *   请求:  {"_route": "execute", "action": "select|insert|update|delete",
 *            "table": "T", "columns": [...], "values": {...},
 *            "where": {"col": val | {"op": val}}, "order_by": [...],
 *            "limit": N, "offset": N}
 *   响应:  {"success": true,  "data": [...]}
 *         {"success": false, "error": "..."}
 *
 * 依赖: libmsg (libus_msg.so), boost::json (-lboost_json), sqlite3 (-lsqlite3)
 */

#include <us/RpcServer.hpp>
#include <us/RpcClient.hpp>

#include <boost/json.hpp>

#include <sqlite3.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <cstdio>
#include <ctime>

namespace json = boost::json;
using namespace pmc::net;

// ============================================================================
// 全局常量
// ============================================================================

static constexpr size_t kMaxMessage = 8192;
static constexpr size_t kMaxExtra   = 4096;

// ============================================================================
// 工具：当前 UTC 时间 ISO 8601
// ============================================================================

static std::string utc_now() {
    auto now = std::chrono::system_clock::now();
    std::time_t t = std::chrono::system_clock::to_time_t(now);
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", std::gmtime(&t));
    return buf;
}

// ============================================================================
// 工具：简单 INI 解析（白名单模式）
// ============================================================================

using IniMap = std::map<std::string, std::map<std::string, std::string>>;

static IniMap parse_ini(const std::string& text) {
    IniMap result;
    std::string section;
    std::istringstream ss(text);
    std::string line;
    while (std::getline(ss, line)) {
        // 去首尾空白
        auto b = line.find_first_not_of(" \t\r\n");
        if (b == std::string::npos) continue;
        line = line.substr(b);
        auto e = line.find_last_not_of(" \t\r\n");
        if (e != std::string::npos) line = line.substr(0, e + 1);

        if (line.empty() || line[0] == '#' || line[0] == ';') continue;
        if (line.front() == '[' && line.back() == ']') {
            section = line.substr(1, line.size() - 2);
            continue;
        }
        auto eq = line.find('=');
        if (eq == std::string::npos || section.empty()) continue;
        std::string key = line.substr(0, eq);
        std::string val = line.substr(eq + 1);
        // 去 key/val 空白
        auto trim = [](std::string& s) {
            auto bb = s.find_first_not_of(" \t");
            auto ee = s.find_last_not_of(" \t");
            s = (bb == std::string::npos) ? "" : s.substr(bb, ee - bb + 1);
        };
        trim(key); trim(val);
        result[section][key] = val;
    }
    return result;
}

// ============================================================================
// 配置
// ============================================================================

struct Config {
    std::string listen_host      = "127.0.0.1";
    int         listen_port      = 8005;
    int         workers          = 4;
    std::string db_url           = "sqlite:///database.db";

    // 日志清理
    int         retention_days   = 30;
    int         max_records      = 1000000;
    int         cleanup_interval = 3600;

    // confs 地址（引导配置，不从 confs 自身获取）
    std::string confs_host       = "127.0.0.1";
    int         confs_port       = 8010;
};

// 从 sqlite:///path 或 sqlite:////abs/path 提取文件路径
static std::string db_url_to_path(const std::string& url) {
    const std::string prefix = "sqlite:///";
    if (url.rfind(prefix, 0) == 0) {
        return url.substr(prefix.size());
    }
    return url;  // 非 sqlite URL，原样返回（暂不支持其他后端）
}

static Config load_config(int argc, char* argv[]) {
    Config cfg;

    // 1. 从环境变量读取 confs 引导地址
    if (const char* h = std::getenv("CONFS_HOST")) cfg.confs_host = h;
    if (const char* p = std::getenv("CONFS_PORT")) cfg.confs_port = std::stoi(p);

    // 2. 从 confs 服务拉取 db.ini（retry_on_refused=true，等待 confs 就绪）
    std::string ini_text;
    try {
        RpcClient confs_client(cfg.confs_host, cfg.confs_port, "db2",
                               /*timeout=*/5.0, /*retry_on_refused=*/true);
        json::value data = confs_client.call("get", {{"name", "db.ini"}});
        if (data.is_object() && data.as_object().contains("content")) {
            ini_text = std::string(data.as_object().at("content").as_string());
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[db2] Failed to fetch db.ini from confs: %s\n", e.what());
        std::exit(1);
    }

    // 3. 解析 INI（白名单字段）
    IniMap ini = parse_ini(ini_text);
    auto get = [&](const std::string& sec, const std::string& key,
                   const std::string& def = "") -> std::string {
        auto si = ini.find(sec);
        if (si == ini.end()) return def;
        auto ki = si->second.find(key);
        return (ki == si->second.end()) ? def : ki->second;
    };

    cfg.listen_host      = get("server",   "host",             cfg.listen_host);
    cfg.listen_port      = std::stoi(get("server",   "port",   std::to_string(cfg.listen_port)));
    cfg.workers          = std::stoi(get("server",   "workers",std::to_string(cfg.workers)));
    cfg.db_url           = get("database", "url",              cfg.db_url);
    cfg.retention_days   = std::stoi(get("logs", "retention_days",  std::to_string(cfg.retention_days)));
    cfg.max_records      = std::stoi(get("logs", "max_records",      std::to_string(cfg.max_records)));
    cfg.cleanup_interval = std::stoi(get("logs", "cleanup_interval", std::to_string(cfg.cleanup_interval)));

    // 4. 环境变量最高优先级覆盖（Docker 兼容）
    if (const char* v = std::getenv("LISTEN_ADDR"))   cfg.listen_host = v;
    if (const char* v = std::getenv("LISTEN_PORT"))   cfg.listen_port = std::stoi(v);
    if (const char* v = std::getenv("WORKERS"))       cfg.workers     = std::stoi(v);
    if (const char* v = std::getenv("DATABASE_URL"))  cfg.db_url      = v;

    // 5. 命令行参数（最高优先级）
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--addr"    || arg == "-a") && i + 1 < argc) cfg.listen_host = argv[++i];
        else if ((arg == "--port" || arg == "-p") && i + 1 < argc) cfg.listen_port = std::stoi(argv[++i]);
        else if ((arg == "--workers" || arg == "-w") && i + 1 < argc) cfg.workers = std::stoi(argv[++i]);
    }

    return cfg;
}

// ============================================================================
// SQLite 连接（per-thread）
// ============================================================================

class DbConn {
public:
    explicit DbConn(const std::string& path) {
        if (sqlite3_open(path.c_str(), &_db) != SQLITE_OK) {
            std::string err = sqlite3_errmsg(_db);
            sqlite3_close(_db);
            _db = nullptr;
            throw std::runtime_error("sqlite3_open failed: " + err);
        }
        _exec_pragma("PRAGMA journal_mode=WAL");
        _exec_pragma("PRAGMA synchronous=NORMAL");
        _exec_pragma("PRAGMA foreign_keys=ON");
        _exec_pragma("PRAGMA busy_timeout=5000");
        _exec_pragma("PRAGMA mmap_size=268435456");
    }

    ~DbConn() {
        if (_db) { sqlite3_close(_db); _db = nullptr; }
    }

    sqlite3* get() const { return _db; }

    // 执行无结果集 SQL（建表、PRAGMA 等）
    void exec(const std::string& sql) {
        char* errmsg = nullptr;
        int rc = sqlite3_exec(_db, sql.c_str(), nullptr, nullptr, &errmsg);
        if (rc != SQLITE_OK) {
            std::string err = errmsg ? errmsg : "unknown";
            sqlite3_free(errmsg);
            throw std::runtime_error("sqlite3_exec failed: " + err);
        }
    }

    DbConn(const DbConn&) = delete;
    DbConn& operator=(const DbConn&) = delete;

private:
    sqlite3* _db = nullptr;

    void _exec_pragma(const std::string& sql) {
        char* errmsg = nullptr;
        sqlite3_exec(_db, sql.c_str(), nullptr, nullptr, &errmsg);
        if (errmsg) sqlite3_free(errmsg);
    }
};

// ============================================================================
// 列元信息（来自 PRAGMA table_info）
// ============================================================================

struct ColInfo {
    std::string name;
    std::string type;  // 原始类型字符串，大写，如 "DATETIME" "TEXT" "INTEGER"
};

// ============================================================================
// DB2Server
// ============================================================================

class DB2Server {
public:
    explicit DB2Server(const Config& cfg)
        : _cfg(cfg), _db_path(db_url_to_path(cfg.db_url)) {}

    void start() {
        // 用主线程连接初始化 schema
        {
            DbConn conn(_db_path);
            _ensure_logs_table(conn);
        }

        _log_internal("INFO", "db2", "db2 starting on "
                      + _cfg.listen_host + ":" + std::to_string(_cfg.listen_port)
                      + ", db=" + _db_path, "", "");

        // 启动后台清理线程
        _cleanup_running = true;
        _cleanup_thread = std::thread(&DB2Server::_cleanup_loop, this);

        // 启动 RPC 服务
        MsgServerConfig srv_cfg;
        srv_cfg.workers = _cfg.workers;

        RpcServer rpc(_cfg.listen_host, _cfg.listen_port, srv_cfg,
            [this](const std::string& route, const std::exception& e) {
                std::fprintf(stderr, "[db2] handler error route=%s: %s\n",
                             route.c_str(), e.what());
            });

        rpc.add_route("execute", [this](const json::object& req) -> json::value {
            return _handle_execute(req);
        });

        rpc.start();

        // rpc.start() 返回后停止清理线程
        _cleanup_running = false;
        _cleanup_cv.notify_all();
        if (_cleanup_thread.joinable()) _cleanup_thread.join();
    }

private:
    const Config& _cfg;
    std::string   _db_path;

    // 表列信息缓存
    std::mutex _schema_mtx;
    std::unordered_map<std::string, std::vector<ColInfo>> _schema_cache;

    // 后台清理线程
    std::atomic<bool>       _cleanup_running{false};
    std::thread             _cleanup_thread;
    std::mutex              _cleanup_mtx;
    std::condition_variable _cleanup_cv;

    // ---- per-thread SQLite 连接 -----------------------------------------------

    DbConn& _get_conn() {
        thread_local std::unique_ptr<DbConn> conn;
        if (!conn) conn = std::make_unique<DbConn>(_db_path);
        return *conn;
    }

    // ---- 列 schema 反射 --------------------------------------------------------

    std::vector<ColInfo> _get_schema(const std::string& table) {
        {
            std::lock_guard<std::mutex> lk(_schema_mtx);
            auto it = _schema_cache.find(table);
            if (it != _schema_cache.end()) return it->second;
        }

        // PRAGMA table_info 返回: cid, name, type, notnull, dflt_value, pk
        DbConn& conn = _get_conn();
        std::string sql = "PRAGMA table_info(" + table + ")";
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) {
            throw std::runtime_error("PRAGMA table_info failed for: " + table);
        }

        std::vector<ColInfo> cols;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            ColInfo ci;
            const char* name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
            const char* type = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
            ci.name = name ? name : "";
            ci.type = type ? type : "";
            // 统一大写便于比较
            std::transform(ci.type.begin(), ci.type.end(), ci.type.begin(), ::toupper);
            cols.push_back(ci);
        }
        sqlite3_finalize(stmt);

        if (cols.empty()) {
            throw std::runtime_error("table not found: " + table);
        }

        std::lock_guard<std::mutex> lk(_schema_mtx);
        _schema_cache[table] = cols;
        return cols;
    }

    // 判断列是否为日期时间类型
    static bool _is_datetime_col(const ColInfo& ci) {
        // 按类型字符串
        if (ci.type.find("DATETIME") != std::string::npos) return true;
        if (ci.type.find("DATE")     != std::string::npos) return true;
        if (ci.type.find("TIME")     != std::string::npos) return true;
        // 按列名约定
        std::string n = ci.name;
        std::transform(n.begin(), n.end(), n.begin(), ::tolower);
        return n.find("_at")   != std::string::npos
            || n.find("time")  != std::string::npos
            || n.find("date")  != std::string::npos;
    }

    // 将 JSON 值转换为 SQLite 绑定字符串（$now 替换为 UTC 时间）
    static std::string _coerce_value(const ColInfo& ci, const json::value& jv) {
        if (jv.is_string()) {
            std::string s(jv.as_string());
            if (_is_datetime_col(ci) && s == "$now") return utc_now();
            return s;
        }
        if (jv.is_int64())  return std::to_string(jv.as_int64());
        if (jv.is_uint64()) return std::to_string(jv.as_uint64());
        if (jv.is_double()) return std::to_string(jv.as_double());
        if (jv.is_bool())   return jv.as_bool() ? "1" : "0";
        if (jv.is_null())   return "";  // 绑定 NULL
        return json::serialize(jv);
    }

    // ---- WHERE 子句构建 --------------------------------------------------------

    struct WhereClause {
        std::string sql;                 // "col1 = ? AND col2 > ?"
        std::vector<std::string> params; // 绑定参数值（顺序对应 ?）
        std::vector<bool>        is_null; // 对应参数是否绑定 NULL
    };

    WhereClause _build_where(const json::object& where,
                             const std::vector<ColInfo>& cols) {
        WhereClause wc;
        std::vector<std::string> parts;

        // 建立列名 -> ColInfo 映射
        std::unordered_map<std::string, ColInfo> col_map;
        for (auto& c : cols) col_map[c.name] = c;

        for (auto& [key, val] : where) {
            std::string col(key);
            ColInfo ci;
            auto it = col_map.find(col);
            if (it != col_map.end()) ci = it->second;
            else { ci.name = col; ci.type = "TEXT"; }

            if (val.is_object()) {
                // {"op": value} 形式
                for (auto& [op_key, op_val] : val.as_object()) {
                    std::string op(op_key);
                    if (op == "eq") {
                        parts.push_back(col + " = ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(op_val.is_null());
                    } else if (op == "ne") {
                        parts.push_back(col + " != ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(op_val.is_null());
                    } else if (op == "gt") {
                        parts.push_back(col + " > ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(false);
                    } else if (op == "lt") {
                        parts.push_back(col + " < ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(false);
                    } else if (op == "ge") {
                        parts.push_back(col + " >= ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(false);
                    } else if (op == "le") {
                        parts.push_back(col + " <= ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(false);
                    } else if (op == "like") {
                        parts.push_back(col + " LIKE ?");
                        wc.params.push_back(_coerce_value(ci, op_val));
                        wc.is_null.push_back(false);
                    } else if (op == "in") {
                        if (!op_val.is_array() || op_val.as_array().empty()) continue;
                        std::string placeholders;
                        for (auto& item : op_val.as_array()) {
                            if (!placeholders.empty()) placeholders += ", ";
                            placeholders += "?";
                            wc.params.push_back(_coerce_value(ci, item));
                            wc.is_null.push_back(item.is_null());
                        }
                        parts.push_back(col + " IN (" + placeholders + ")");
                    }
                }
            } else {
                // 直接等于
                parts.push_back(col + " = ?");
                wc.params.push_back(_coerce_value(ci, val));
                wc.is_null.push_back(val.is_null());
            }
        }

        for (size_t i = 0; i < parts.size(); ++i) {
            if (i > 0) wc.sql += " AND ";
            wc.sql += parts[i];
        }
        return wc;
    }

    // ---- 参数绑定辅助 ----------------------------------------------------------

    static void _bind_params(sqlite3_stmt* stmt, int start_idx,
                             const std::vector<std::string>& params,
                             const std::vector<bool>& is_null) {
        for (size_t i = 0; i < params.size(); ++i) {
            int idx = start_idx + static_cast<int>(i);
            if (is_null[i]) {
                sqlite3_bind_null(stmt, idx);
            } else {
                sqlite3_bind_text(stmt, idx, params[i].c_str(), -1, SQLITE_TRANSIENT);
            }
        }
    }

    // ---- execute 路由 ----------------------------------------------------------

    json::value _handle_execute(const json::object& req) {
        // 提取公共字段
        std::string action = "select";
        if (auto it = req.find("action"); it != req.end() && it->value().is_string())
            action = std::string(it->value().as_string());

        if (!req.contains("table") || !req.at("table").is_string())
            throw std::runtime_error("missing required field: table");
        std::string table(req.at("table").as_string());

        // 验证表名（仅允许字母数字下划线，防止 SQL 注入）
        for (char c : table) {
            if (!std::isalnum(static_cast<unsigned char>(c)) && c != '_')
                throw std::runtime_error("invalid table name: " + table);
        }

        std::vector<ColInfo> cols = _get_schema(table);

        if (action == "select") return _do_select(req, table, cols);
        if (action == "insert") return _do_insert(req, table, cols);
        if (action == "update") return _do_update(req, table, cols);
        if (action == "delete") return _do_delete(req, table, cols);
        throw std::runtime_error("unknown action: " + action);
    }

    // ---- SELECT ----------------------------------------------------------------

    json::value _do_select(const json::object& req,
                           const std::string& table,
                           const std::vector<ColInfo>& cols) {
        DbConn& conn = _get_conn();

        // columns
        std::string col_expr = "*";
        if (auto it = req.find("columns");
            it != req.end() && it->value().is_array() && !it->value().as_array().empty()) {
            col_expr.clear();
            for (auto& c : it->value().as_array()) {
                if (!col_expr.empty()) col_expr += ", ";
                col_expr += std::string(c.as_string());
            }
        }

        std::string sql = "SELECT " + col_expr + " FROM " + table;
        WhereClause wc;

        if (auto it = req.find("where"); it != req.end() && it->value().is_object())
            wc = _build_where(it->value().as_object(), cols);
        if (!wc.sql.empty()) sql += " WHERE " + wc.sql;

        // order_by
        if (auto it = req.find("order_by");
            it != req.end() && it->value().is_array()) {
            std::string order;
            for (auto& ob : it->value().as_array()) {
                if (!ob.is_object()) continue;
                auto& o = ob.as_object();
                if (!o.contains("column")) continue;
                if (!order.empty()) order += ", ";
                order += std::string(o.at("column").as_string());
                if (o.contains("desc") && o.at("desc").as_bool()) order += " DESC";
            }
            if (!order.empty()) sql += " ORDER BY " + order;
        }

        // limit / offset
        if (auto it = req.find("limit"); it != req.end() && it->value().is_int64())
            sql += " LIMIT " + std::to_string(it->value().as_int64());
        if (auto it = req.find("offset"); it != req.end() && it->value().is_int64())
            sql += " OFFSET " + std::to_string(it->value().as_int64());

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK) {
            throw std::runtime_error(std::string("prepare failed: ") + sqlite3_errmsg(conn.get()));
        }
        _bind_params(stmt, 1, wc.params, wc.is_null);

        // 获取结果列名（可能是 * 展开后的实际列名）
        int ncols = sqlite3_column_count(stmt);
        std::vector<std::string> result_cols;
        result_cols.reserve(ncols);
        for (int i = 0; i < ncols; ++i) {
            const char* n = sqlite3_column_name(stmt, i);
            result_cols.push_back(n ? n : "");
        }

        json::array rows;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            json::object row;
            for (int i = 0; i < ncols; ++i) {
                int ctype = sqlite3_column_type(stmt, i);
                if (ctype == SQLITE_NULL) {
                    row[result_cols[i]] = nullptr;
                } else if (ctype == SQLITE_INTEGER) {
                    row[result_cols[i]] = sqlite3_column_int64(stmt, i);
                } else if (ctype == SQLITE_FLOAT) {
                    row[result_cols[i]] = sqlite3_column_double(stmt, i);
                } else {
                    const unsigned char* txt = sqlite3_column_text(stmt, i);
                    row[result_cols[i]] = txt ? reinterpret_cast<const char*>(txt) : "";
                }
            }
            rows.push_back(row);
        }
        sqlite3_finalize(stmt);
        return rows;
    }

    // ---- INSERT ----------------------------------------------------------------

    json::value _do_insert(const json::object& req,
                           const std::string& table,
                           const std::vector<ColInfo>& cols) {
        if (!req.contains("values") || !req.at("values").is_object())
            throw std::runtime_error("insert requires 'values' object");

        DbConn& conn = _get_conn();
        const json::object& vals = req.at("values").as_object();

        // 建列名 -> ColInfo 映射
        std::unordered_map<std::string, ColInfo> col_map;
        for (auto& c : cols) col_map[c.name] = c;

        std::string col_list, ph_list;
        std::vector<std::string> params;
        std::vector<bool> is_null;

        for (auto& [k, v] : vals) {
            if (!col_list.empty()) { col_list += ", "; ph_list += ", "; }
            col_list += std::string(k);
            ph_list += "?";
            ColInfo ci;
            auto it = col_map.find(std::string(k));
            if (it != col_map.end()) ci = it->second;
            else { ci.name = std::string(k); ci.type = "TEXT"; }
            params.push_back(_coerce_value(ci, v));
            is_null.push_back(v.is_null());
        }

        std::string sql = "INSERT INTO " + table + " (" + col_list + ") VALUES (" + ph_list + ")";
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK)
            throw std::runtime_error(std::string("prepare failed: ") + sqlite3_errmsg(conn.get()));

        _bind_params(stmt, 1, params, is_null);
        int rc = sqlite3_step(stmt);
        sqlite3_finalize(stmt);
        if (rc != SQLITE_DONE)
            throw std::runtime_error(std::string("insert failed: ") + sqlite3_errmsg(conn.get()));

        sqlite3_int64 rowid = sqlite3_last_insert_rowid(conn.get());
        return json::array{json::object{{"lastrowid", rowid}}};
    }

    // ---- UPDATE ----------------------------------------------------------------

    json::value _do_update(const json::object& req,
                           const std::string& table,
                           const std::vector<ColInfo>& cols) {
        if (!req.contains("values") || !req.at("values").is_object())
            throw std::runtime_error("update requires 'values' object");

        DbConn& conn = _get_conn();
        const json::object& vals = req.at("values").as_object();

        std::unordered_map<std::string, ColInfo> col_map;
        for (auto& c : cols) col_map[c.name] = c;

        std::string set_clause;
        std::vector<std::string> params;
        std::vector<bool> is_null;

        for (auto& [k, v] : vals) {
            if (!set_clause.empty()) set_clause += ", ";
            set_clause += std::string(k) + " = ?";
            ColInfo ci;
            auto it = col_map.find(std::string(k));
            if (it != col_map.end()) ci = it->second;
            else { ci.name = std::string(k); ci.type = "TEXT"; }
            params.push_back(_coerce_value(ci, v));
            is_null.push_back(v.is_null());
        }

        std::string sql = "UPDATE " + table + " SET " + set_clause;
        WhereClause wc;
        if (auto it = req.find("where"); it != req.end() && it->value().is_object())
            wc = _build_where(it->value().as_object(), cols);
        if (!wc.sql.empty()) {
            sql += " WHERE " + wc.sql;
            params.insert(params.end(), wc.params.begin(), wc.params.end());
            is_null.insert(is_null.end(), wc.is_null.begin(), wc.is_null.end());
        }

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK)
            throw std::runtime_error(std::string("prepare failed: ") + sqlite3_errmsg(conn.get()));

        _bind_params(stmt, 1, params, is_null);
        int rc = sqlite3_step(stmt);
        sqlite3_finalize(stmt);
        if (rc != SQLITE_DONE)
            throw std::runtime_error(std::string("update failed: ") + sqlite3_errmsg(conn.get()));

        int rowcount = sqlite3_changes(conn.get());
        return json::array{json::object{{"rowcount", rowcount}}};
    }

    // ---- DELETE ----------------------------------------------------------------

    json::value _do_delete(const json::object& req,
                           const std::string& table,
                           const std::vector<ColInfo>& cols) {
        DbConn& conn = _get_conn();

        std::string sql = "DELETE FROM " + table;
        WhereClause wc;
        if (auto it = req.find("where"); it != req.end() && it->value().is_object())
            wc = _build_where(it->value().as_object(), cols);
        if (!wc.sql.empty()) sql += " WHERE " + wc.sql;

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK)
            throw std::runtime_error(std::string("prepare failed: ") + sqlite3_errmsg(conn.get()));

        _bind_params(stmt, 1, wc.params, wc.is_null);
        int rc = sqlite3_step(stmt);
        sqlite3_finalize(stmt);
        if (rc != SQLITE_DONE)
            throw std::runtime_error(std::string("delete failed: ") + sqlite3_errmsg(conn.get()));

        int rowcount = sqlite3_changes(conn.get());
        return json::array{json::object{{"rowcount", rowcount}}};
    }

    // ---- Logs 表初始化 ---------------------------------------------------------

    void _ensure_logs_table(DbConn& conn) {
        conn.exec(R"(
            CREATE TABLE IF NOT EXISTS Logs (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT NOT NULL,
                service  TEXT NOT NULL,
                level    TEXT NOT NULL,
                message  TEXT NOT NULL,
                trace_id TEXT,
                extra    TEXT
            )
        )");
        conn.exec("CREATE INDEX IF NOT EXISTS idx_logs_service_ts ON Logs(service, ts)");
        conn.exec("CREATE INDEX IF NOT EXISTS idx_logs_level_ts   ON Logs(level, ts)");
        conn.exec("CREATE INDEX IF NOT EXISTS idx_logs_ts         ON Logs(ts)");
        conn.exec("CREATE INDEX IF NOT EXISTS idx_logs_trace      ON Logs(trace_id)");
    }

    // ---- 内部日志直写（绕过 RPC，防递归） --------------------------------------

    void _log_internal(const std::string& level, const std::string& service,
                       std::string message, const std::string& trace_id,
                       const std::string& extra) {
        if (message.size() > kMaxMessage) message.resize(kMaxMessage);
        std::string ex = extra;
        if (ex.size() > kMaxExtra) ex.resize(kMaxExtra);

        try {
            DbConn& conn = _get_conn();
            std::string sql =
                "INSERT INTO Logs (ts, service, level, message, trace_id, extra) "
                "VALUES (?, ?, ?, ?, ?, ?)";
            sqlite3_stmt* stmt = nullptr;
            if (sqlite3_prepare_v2(conn.get(), sql.c_str(), -1, &stmt, nullptr) != SQLITE_OK)
                return;

            std::string ts = utc_now();
            sqlite3_bind_text(stmt, 1, ts.c_str(),      -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, service.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, level.c_str(),   -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, message.c_str(), -1, SQLITE_TRANSIENT);
            if (trace_id.empty()) sqlite3_bind_null(stmt, 5);
            else sqlite3_bind_text(stmt, 5, trace_id.c_str(), -1, SQLITE_TRANSIENT);
            if (ex.empty()) sqlite3_bind_null(stmt, 6);
            else sqlite3_bind_text(stmt, 6, ex.c_str(), -1, SQLITE_TRANSIENT);

            sqlite3_step(stmt);
            sqlite3_finalize(stmt);
        } catch (...) {}
    }

    // ---- 后台日志清理线程 -------------------------------------------------------

    void _cleanup_loop() {
        while (_cleanup_running) {
            std::unique_lock<std::mutex> lk(_cleanup_mtx);
            _cleanup_cv.wait_for(lk,
                std::chrono::seconds(_cfg.cleanup_interval),
                [this] { return !_cleanup_running.load(); });
            if (!_cleanup_running) break;
            _do_cleanup();
        }
    }

    void _do_cleanup() {
        try {
            DbConn& conn = _get_conn();

            // 1. 删除超过 retention_days 的记录
            std::string cutoff_sql =
                "DELETE FROM Logs WHERE ts < datetime('now', '-"
                + std::to_string(_cfg.retention_days) + " days')";
            conn.exec(cutoff_sql);
            int expired = sqlite3_changes(conn.get());

            // 2. 若总条数超过 max_records，删除最旧的超出部分
            int trimmed = 0;
            {
                sqlite3_stmt* stmt = nullptr;
                std::string cnt_sql = "SELECT COUNT(*) FROM Logs";
                if (sqlite3_prepare_v2(conn.get(), cnt_sql.c_str(), -1, &stmt, nullptr) == SQLITE_OK) {
                    if (sqlite3_step(stmt) == SQLITE_ROW) {
                        int total = sqlite3_column_int(stmt, 0);
                        if (total > _cfg.max_records) {
                            int to_delete = total - _cfg.max_records;
                            sqlite3_finalize(stmt);
                            std::string trim_sql =
                                "DELETE FROM Logs WHERE id IN "
                                "(SELECT id FROM Logs ORDER BY id ASC LIMIT "
                                + std::to_string(to_delete) + ")";
                            conn.exec(trim_sql);
                            trimmed = sqlite3_changes(conn.get());
                        } else {
                            sqlite3_finalize(stmt);
                        }
                    } else {
                        sqlite3_finalize(stmt);
                    }
                }
            }

            if (expired > 0 || trimmed > 0) {
                _log_internal("INFO", "db2",
                    "cleanup: expired=" + std::to_string(expired)
                    + " trimmed=" + std::to_string(trimmed), "", "");
            }
        } catch (const std::exception& e) {
            std::fprintf(stderr, "[db2] cleanup error: %s\n", e.what());
        }
    }
};

// ============================================================================
// main
// ============================================================================

int main(int argc, char* argv[]) {
    try {
        Config cfg = load_config(argc, argv);

        std::printf("[db2] listen=%s:%d workers=%d db=%s\n",
                    cfg.listen_host.c_str(), cfg.listen_port,
                    cfg.workers, cfg.db_url.c_str());
        std::fflush(stdout);

        DB2Server server(cfg);
        server.start();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[db2] fatal: %s\n", e.what());
        return 1;
    }
    return 0;
}
