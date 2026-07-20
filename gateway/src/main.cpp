#include <us/HttpServer.hpp>
#include <us/HttpClient.hpp>
#include <us/MyRSA.hpp>
#include <us/Base64.hpp>
#include <us/SHA256.hpp>
#include <us/Sqlite3.hpp>
#include <iostream>
#include <typeinfo>
#include <memory>
#include <csignal>
#include <boost/json.hpp>
#include <boost/program_options.hpp>
#include <openssl/provider.h>
#include "ExpBase.hpp"
#include "Conf.hpp"

using namespace pmc::net;
using namespace qing;
namespace json = boost::json;
namespace po = boost::program_options;

constexpr const char *_VER = "0.2.1";


class DatabaseException: public ExceptionBase {
public:
	using ExceptionBase::ExceptionBase;

	std::string build_message() const override {
		return "[Database]" + ExceptionBase::build_message();
	}
};

class HttpException: public ExceptionBase {
public:
	HttpException(int code, const std::string& msg,
			int http_status, const std::string& url,
			const std::source_location& loc = std::source_location::current())
		: ExceptionBase(code, msg, loc)
		, m_http_status(http_status)
		, m_url(url) {}
	std::string build_message() const override {
		return "[Http]" + ExceptionBase::build_message();
	}
	int status() const {
		return m_http_status;
	}
	const std::string& url() const {
		return m_url;
	}
private:
    int m_http_status;
    std::string m_url;
};

void db_init();
void registerRoutes(HttpServer &server);

http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main);

http::response<http::string_body> handleProxy(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		const char *method
);

http::response<http::string_body> handleLogin(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);

http::response<http::string_body> handleRegister(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params);


/*  headers can't processed */
bool is_special_header(const std::string &header);

/* configure items */
static std::string	_ADDR;
static int		_PORT;
static std::string	_DEFT_URL;
static std::string	_DB_PATH;
static std::string	_DEFT_ADMIN_USER = "admin";
static std::string	_DEFT_ADMIN_PASS = "44112233";

/* instance items */
static std::unique_ptr<HttpServer>		_SERVE;
static std::unique_ptr<MyRSA::Generator>	_KEYGEN;
static std::unique_ptr<Sqlite3>			_SQLDB;

void signalHandler(int signum) {

	std::cout << "Receive close signal, prepare to shutdown...\n";
	if (_SERVE) {
		_SERVE->stop();
	}
}

void set_signal_action() {

	struct sigaction sa;
	sa.sa_handler = signalHandler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;
	sigaction(SIGINT, &sa, nullptr);
	sigaction(SIGTERM, &sa, nullptr);
}

int main(int argc, char **argv) {
	po::options_description desc("cpp language web gateway");
	desc.add_options()
		("help,h",					"output help message")
		("version,v",					"output version message")
		("input,i", 	po::value<std::string>(),	"input a configure text file")
	;
	po::variables_map vm;
	/* parse commandline params */
	{
		try {
			po::store(po::parse_command_line(argc, argv, desc), vm);
			po::notify(vm);
		}

		catch (const std::exception &e) {
			std::cerr << "ERROR: "  << e.what() << std::endl;
			std::cerr << "using --help to check options message" << std::endl;
			throw e;
		}
	}

	if (vm.count("help")) {
		std::cout << desc << std::endl;
		return 0;
	}

	if (!vm.count("input")) {
		std::cerr << "Please input configure file via -i"  << std::endl;
		return 0;
	}

	/* parse configure file */
	{
		try {

			ConfIni cf(vm["input"].as<std::string>());
			_ADDR = cf.get<std::string>("Server.address", "");
			_PORT = cf.get<int> ("Server.port", -1);
			_DEFT_URL = cf.get<std::string>("Proxy.default", "");
			_DB_PATH = cf.get<std::string>("Database.path", "");
		}
		catch (ConfIni::IniConfigureFileParseException &e) {
			std::cerr << "ERROR: "  << e.what() << std::endl;
			std::cerr << "ini format input file error" << std::endl;
			return 1;
		}
	}

	if (_ADDR == "" || _PORT == -1) {
		std::cerr << "Please setup addree and port in Serve section in configure file";
		std::cerr << std::endl;
		return 0;
	}
		
	_SERVE = std::make_unique<HttpServer> (_ADDR, _PORT, "full_chain.crt", "server.key", 4);
	_KEYGEN = std::make_unique<MyRSA::Generator> ();
	_SQLDB = std::make_unique<Sqlite3> (_DB_PATH);

	db_init();
	registerRoutes(*_SERVE);
	set_signal_action();

	_SERVE->start();
	_SERVE->run();

}

void db_init() {

	std::string sql = "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='Users'";

	auto flag = false;
	{
		auto stmt = _SQLDB->stmt(sql);
		if (stmt.step()) {
			auto num = stmt.read_int(0);
			if (num == 0) {
				flag = true;
			}
		}
	}

	if (flag) {
		std::cout << "Start database init..." << std::endl;
		std::string sql_crtt = "-- create users table, username as email\n"
			"CREATE TABLE Users (\n"
				"id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
				"email TEXT UNIQUE NOT NULL,\n"
				"password_hash TEXT NOT NULL,\n"
				"create_at DATETIME DEFAULT CURRENT_TIMESTAMP,\n"
				"last_login DATETIME,\n"
				"is_active BOOLEAN DEFAULT 1\n"
			");\n\n"

			"CREATE INDEX idx_users_email ON users(email);\n";

		_SQLDB->exec(sql_crtt);

		{
			auto password_hash = SHA256::sha256(_DEFT_ADMIN_PASS);
			std::cout << password_hash << std::endl;
			std::string sql_crta = "INSERT INTO Users(email, password_hash, last_login) VALUES (?, ?, datetime('now', 'localtime'))";
			auto stmt = _SQLDB->stmt(sql_crta);
			stmt.bind_text(1, _DEFT_ADMIN_USER);
			stmt.bind_text(2, password_hash);
			if (stmt.step()) {
				std::cout << stmt.read_text(1) << std::endl;
			}
		}

		std::cout << "done" << std::endl;
	}

	else {
		std::cout << "The database already init" << std::endl;
	}
}



void registerRoutes(HttpServer &server) {

	server.post("/login", [](
			const http::request<http::string_body>& req,
			const std::unordered_map<std::string, std::string>& params
	) -> http::response<http::string_body>  {
		return wrapperHttp(req, params, [](
				const auto& req, const auto& params
		) {
			return handleLogin(req, params);
		});
	});

	server.post("/register", [](const auto& req, const auto& params) {
		return handleRegister(req, params);
	});

	server.get("*", [](const auto& req, const auto& params) {
		return handleProxy(req, params, "get");
	});

	server.post("*", [](const auto& req, const auto& params) {
		return handleProxy(req, params, "post");
	});
}

http::response<http::string_body> wrapperHttp(
		const http::request<http::string_body>& req,
		const std::unordered_map<std::string, std::string>& params,
		HttpRequestHandler _main)

try {

	return _main(req, params);
}

catch (const std::exception& e) {

	http::response<http::string_body> res;
	res.version(req.version());
	const auto& ti = typeid(e);
	json::value jv_r = {
		{"success", false},
		{"message", std::string("<") + ti.name() + "> " + e.what()}
	};
	res.set(http::field::content_type, "text/plain");
	res.result(http::status::internal_server_error);
	res.body() = json::serialize(jv_r);
	res.prepare_payload();
	return res;
}

/* curl -X POST http://127.0.0.1:9201/login -d '{"username": "qing", "password": "12345678"}'  */
http::response<http::string_body> handleLogin(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	try {
		//std::cout << req.body() << std::endl;
		json::value jv;
		try {
			jv = json::parse(req.body());
		}
		catch (const boost::wrapexcept<boost::system::system_error>& e) {
			THROW_EX_CUSTOM(HttpException, 2001, "Invalid_json_Argument",
                            400, "/login");
		}

		
		if (!jv.is_object()) {
			THROW_EX_CUSTOM(HttpException, 2002, "Invalid_json_object_Argument",
                            400, "/login");
		}
		auto& obj = jv.as_object();
		auto username = std::string(obj.at("username").as_string());
		auto password = std::string(obj.at("password").as_string());
		auto password_hash = SHA256::sha256(password);
		std::cout  << username << " " << password_hash << std::endl;

		std::string sql = "SELECT COUNT(*) FROM Users WHERE email=? AND password_hash=?;";
		auto stmt = _SQLDB->stmt(sql);
		stmt.bind_text(1, username);
		stmt.bind_text(2, password_hash);
		if (stmt.step() && stmt.read_int(0) == 1) {
			//update
		}

		else {
			THROW_EX_CUSTOM(HttpException, 2101, "Invalid Username or Password",
                            401, "/login");
		}

		auto _PRIV = MyRSA::Private_Key::from_pem(_KEYGEN->get_private_key_pem());


		//int  created_at = 0;
		int  keep_time  = 3600;	// second

		json::value jv_r = {
			{"user", username},
			//{"created_at", created_at},
			{"keep_time", keep_time}
		};

		std::string data = json::serialize(jv_r);
		std::string token = Base64::base64_encode(data);
		std::string sign = _PRIV.Sign(data);

		res.set("Connection", "close");
		res.result(http::status::ok);
		res.body() = token + "_" + sign;
	}

	catch (const HttpException& e) {
		
		json::value jv_r = {
			{"success", false},
			{"message", e.what()}
		};
		res.set(http::field::content_type, "application/json");
		res.result(e.status());
		res.body() = json::serialize(jv_r);
	}

	res.prepare_payload();
	return res;
}

/* curl -X POST http://127.0.0.1:9201/register -d '{"username": "qing", "password": "12345678"}'  */
/* FIXME: strong password check */
http::response<http::string_body> handleRegister(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params)
{
	http::response<http::string_body> res;
	res.version(req.version());

	try {

		auto jv = json::parse(req.body());
		if (!jv.is_object()) {
			throw std::runtime_error("400");
		}
		auto& obj = jv.as_object();
		auto username = std::string(obj.at("username").as_string());
		auto password = std::string(obj.at("password").as_string());

		auto password_hash = SHA256::sha256(password);

		std::string sql = "INSERT INTO Users (email, password_hash, last_login) VALUES (?, ?, datetime('now', 'localtime'));";
		auto stmt = _SQLDB->stmt(sql);
		stmt.bind_text(1, username);
		stmt.bind_text(2, password_hash);
		stmt.step();

		json::value jv_r = {{"success", true}};
		res.result(http::status::ok);
		res.body() = json::serialize(jv_r);
	}
	//catch

	catch (const std::exception& e) {
		const auto& ti = typeid(e);
		res.set(http::field::content_type, "text/plain");
		res.result(http::status::internal_server_error);
		res.body() = std::string("<") + ti.name() + "> " + e.what();
	}

	res.prepare_payload();
	return res;
}

/* curl -H "Target-Url: https://www.baidu.com" http://127.0.0.1:9201 -X GET */
http::response<http::string_body> handleProxy(
	const http::request<http::string_body>& req,
	const std::unordered_map<std::string, std::string>& params,
	const char *method)
{
	http::response<http::string_body> res;
	res.version(req.version());

	try {
		auto _PUBL = MyRSA::Public_Key::from_pem(_KEYGEN->get_public_key_pem());

		std::string url = "";
		auto flag = false;
		{
			auto it = req.base().find("Target-Url");
			if (it != req.base().end()) {
				url = req["Target-Url"];
			}

			it = req.base().find("target-url");
			if (it != req.base().end()) {
				url = req["target-url"];
			}

			if (url == "") {
				url =  _DEFT_URL;
				flag = true;
			}

			url += "/";
			url += req.target();
		}

		/* check auth */
		std::string auth = "";
		{
			auto it = req.base().find("Authorization");
			if (it == req.base().end() && !flag) {
				throw std::runtime_error("401");
			}
			std::string raw = req["Authorization"];
			if (raw.substr(0, 7) == "Bearer ") {
				raw = raw.substr(7, raw.size() - 7);
				size_t idx  = raw.find("_");
				if (idx == std::string::npos) {
					throw std::runtime_error("Header 400 Bad Request");
				}
				std::string data = raw.substr(0, idx);
				std::string sign = raw.substr(idx+1, raw.size()-idx-1);
				auth = Base64::base64_decode_to_string(data);
				if (!_PUBL.Verify(auth, sign)) {
					throw std::runtime_error("Header 401 Unauthorized");
				}
				std::cout << "auth data: " << auth  << std::endl;
			}
		}

		auto callback = [&res](const auto& ret) {
			res.body() += ret;	// TODO: too big file
		};


		auto client = HttpClient();
		for (auto const& field: req) {
			if (is_special_header(field.name_string()))
				continue;
			auto h = std::string();
			h += field.name_string();
			h += ": ";
			h += field.value();
			client.set_header(h);
		}


		if (method == "get")
			client.get_stream_sync(url, callback);
	
		else if (method == "post") {
			std::string payload = req.body();
			client.post_stream_sync(url, payload, callback);
		}


		for (const auto& pair: client.get_response_headers() ) {
			res.set(pair.first, pair.second);
		}
		res.set("Connection", "close");
		res.result(client.get_response_code());
	}
	//catch

	catch (const std::exception& e) {
		const auto& ti = typeid(e);
		res.set(http::field::content_type, "text/plain");
		res.result(http::status::internal_server_error);
		json::value jv_r = {
			{"success", false},
			{"err_name", ti.name()},
			{"err_message", e.what()}
		};
		res.body() = json::serialize(jv_r);
	}

	res.prepare_payload();
	return res;
}

/* they are special because they point to raw request */
bool is_special_header(const std::string &header) {

	return  header == "Host" || header == "connection"
		|| header == "Accept-Encoding";
}

