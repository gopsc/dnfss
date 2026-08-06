#include <cstdint>
#include <iostream>
#include <memory>
#include <sqlite3.h>
#include <string>
#include <type_traits>
#include <vector>
namespace qing {


	class Sqlite3_stmt {
	public:
		Sqlite3_stmt(sqlite3 *db, const char *sql) {
			int rc = sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr);
			if (rc != SQLITE_OK) {
				std::string err = sqlite3_errmsg(db);
				std::cerr << "Failed to prepare sql stmt: " << err << std::endl;
				throw std::runtime_error("Failed to prepare sql stmt: " + err);
			}
		}

		~Sqlite3_stmt() {
			if (stmt) {
				sqlite3_finalize(stmt);
				stmt = nullptr;
			}
		}

		Sqlite3_stmt(const Sqlite3_stmt&) = delete;
		Sqlite3_stmt& operator=(const Sqlite3_stmt&) = delete;

		Sqlite3_stmt(Sqlite3_stmt&& other) noexcept : stmt(other.stmt) {
			other.stmt = nullptr;
		}

		Sqlite3_stmt& operator=(Sqlite3_stmt&& other) noexcept {
			if (this != &other) {
				if (stmt) sqlite3_finalize(stmt);
				stmt = other.stmt;
				other.stmt = nullptr;
			}
			return *this;
		}

		void bind_int(int idx, int data) {
			int rc = sqlite3_bind_int(stmt, idx, data);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind an integer to sql stmt");
			}
		}

		void bind_int64(int idx, int64_t data) {
			int rc = sqlite3_bind_int64(stmt, idx, data);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind an int64 to sql stmt");
			}
		}

		void bind_double(int idx, double data) {
			int rc = sqlite3_bind_double(stmt, idx, data);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind a double to sql stmt");
			}
		}

		void bind_text(int idx, const std::string &data) {
			int rc = sqlite3_bind_text(stmt, idx, data.c_str(), -1, SQLITE_TRANSIENT);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind a text to sql stmt");
			}
		}

		void bind_blob(int idx, const std::vector<unsigned char>& data) {
			int rc = sqlite3_bind_blob(stmt, idx, data.data(), static_cast<int>(data.size()), SQLITE_TRANSIENT);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind a blob to sql stmt");
			}
		}

		void bind_null(int idx) {
			int rc = sqlite3_bind_null(stmt, idx);
			if (rc != SQLITE_OK) {
				throw std::runtime_error("Failed to bind null to sql stmt");
			}
		}

		bool step() {
			int rc = sqlite3_step(stmt);
			if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
				throw std::runtime_error("Failed to query");
			}
			return rc == SQLITE_ROW && rc != SQLITE_DONE;
		}

		// 执行无结果集语句（INSERT / UPDATE / DELETE / DDL），返回影响行数
		int execute() {
			int rc = sqlite3_step(stmt);
			if (rc != SQLITE_DONE) {
				throw std::runtime_error("Failed to execute statement");
			}
			return sqlite3_changes(sqlite3_db_handle(stmt));
		}

		/* 
		 * int len = sqlite3_column_bytes(stmt, 1); // 获取字节长度（不含结束符）
		 * std::string name(reinterpret_cast<const char*>(rawName), len);
		 */
		int read_int(int idx) {
			return sqlite3_column_int(stmt, idx);
		}

		int64_t read_int64(int idx) {
			return sqlite3_column_int64(stmt, idx);
		}

		double read_double(int idx) {
			return sqlite3_column_double(stmt, idx);
		}

		// 返回指向 statement 内部缓冲区的指针，生命周期与当前 statement 绑定。
		// statement finalize 或 step 后指针可能失效；如需长期保存请复制到 std::string。
		const unsigned char *read_text(int idx) {
			return  sqlite3_column_text(stmt, idx);
		}

	private:
		sqlite3_stmt* stmt = nullptr;
	};

	class Sqlite3 {
	
	public:
		Sqlite3(const std::string& filename) {
			int rc = sqlite3_open(filename.c_str(), &db);
			if (rc != SQLITE_OK) {
				std::string err = sqlite3_errmsg(db);
				std::cerr << "Failed to open database: " << err << std::endl;
				sqlite3_close(db);
				throw std::runtime_error("Failed to open sqlite3 database: " + err);
			}
		}

		~Sqlite3() {
			if (db) {
				sqlite3_close_v2(db);
				db = nullptr;
			}
		}

		Sqlite3(const Sqlite3&) = delete;
		Sqlite3& operator=(const Sqlite3&) = delete;

		Sqlite3(Sqlite3&& other) noexcept : db(other.db) {
			other.db = nullptr;
		}

		Sqlite3& operator=(Sqlite3&& other) noexcept {
			if (this != &other) {
				if (db) sqlite3_close_v2(db);
				db = other.db;
				other.db = nullptr;
			}
			return *this;
		}

		// 仅用于硬编码 SQL；禁止拼接用户输入！
		bool exec(const std::string& sql) {
			char *errMsg = nullptr;
			int rc = sqlite3_exec(db, sql.c_str(), 0, 0, &errMsg);
			if (rc != SQLITE_OK) {
				std::string err = errMsg ? errMsg : "unknown error";
				std::cerr << "Failed to execute at sqlite3: " << err << std::endl;
				sqlite3_free(errMsg);
				return false;
			}

			return true;
		}

		// 参数化执行 INSERT / UPDATE / DELETE / DDL，支持 int / int64_t / double /
		// std::string / const char* / nullptr / std::vector<unsigned char>
		template<typename... Args>
		int exec(const std::string& sql, Args&&... args) {
			Sqlite3_stmt s(db, sql.c_str());
			int idx = 1;
			(_bind_one(s, idx++, std::forward<Args>(args)), ...);
			return s.execute();
		}

		Sqlite3_stmt stmt(const std::string& sql) {
			return Sqlite3_stmt(db, sql.c_str());
		}

	private:
		sqlite3* db = nullptr;

		template<typename T>
		static void _bind_one(Sqlite3_stmt& s, int idx, T&& val) {
			using U = std::decay_t<T>;
			if constexpr (std::is_same_v<U, std::nullptr_t>) {
				s.bind_null(idx);
			} else if constexpr (std::is_same_v<U, int> || std::is_same_v<U, short> || std::is_same_v<U, bool>) {
				s.bind_int(idx, static_cast<int>(val));
			} else if constexpr (std::is_same_v<U, int64_t> || std::is_same_v<U, long> || std::is_same_v<U, long long>) {
				s.bind_int64(idx, static_cast<int64_t>(val));
			} else if constexpr (std::is_same_v<U, double> || std::is_same_v<U, float>) {
				s.bind_double(idx, static_cast<double>(val));
			} else if constexpr (std::is_same_v<U, std::string>) {
				s.bind_text(idx, val);
			} else if constexpr (std::is_same_v<U, const char*>) {
				s.bind_text(idx, std::string(val));
			} else if constexpr (std::is_same_v<U, std::vector<unsigned char>>) {
				s.bind_blob(idx, val);
			} else {
				static_assert(!std::is_same_v<U, U>, "unsupported bind type");
			}
		}
	};
}
