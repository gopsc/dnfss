#include <iostream>
#include <memory>
#include <sqlite3.h>
namespace qing {


	class Sqlite3_stmt {
	public:
		Sqlite3_stmt(sqlite3 *db, const char *sql) {
			int rc = sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr);
			if (rc != SQLITE_OK) {
				std::cerr << "Failed to prepare sql stmt: " << sqlite3_errmsg(db) << std::endl;
				throw std::runtime_error("Failed to prepare sql stmt");
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
				std::cerr << "Failed to bind an integer to sql stmt." << std::endl;
				throw std::runtime_error("Failed to bind an integer to sql stmt");
			}
		}

		void bind_text(int idx, const std::string &data) {
			int rc = sqlite3_bind_text(stmt, idx, data.c_str(), -1, SQLITE_TRANSIENT);
			if (rc != SQLITE_OK) {
				std::cerr << "Failed to bind a text to sql stmt." << std::endl;
				throw std::runtime_error("Failed to bind a text to sql stmt");
			}
		}

		bool step() {
			int rc = sqlite3_step(stmt);
			if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
				throw std::runtime_error("Failed to query");
			}
			return rc == SQLITE_ROW && rc != SQLITE_DONE;
		}

		/* 
		 * int len = sqlite3_column_bytes(stmt, 1); // 获取字节长度（不含结束符）
		 * std::string name(reinterpret_cast<const char*>(rawName), len);
		 */
		int read_int(int idx) {
			return sqlite3_column_int(stmt, idx);
		}


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
				std::cerr << "Failed to open database: " << sqlite3_errmsg(db) << std::endl;
				sqlite3_close(db);
				throw std::runtime_error("Failed to open sqlite3 database.");
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

		bool exec(const std::string& sql) {
			char *errMsg = nullptr;
			int rc = sqlite3_exec(db, sql.c_str(), 0, 0, &errMsg);
			if (rc != SQLITE_OK) {
				std::cerr << "Failed to execute at sqlite3: " << errMsg << std::endl;
				sqlite3_free(errMsg);
				return false;
			}

			return true;
		}

		Sqlite3_stmt stmt(const std::string& sql) {
			return Sqlite3_stmt(db, sql.c_str());
		}

	private:
		sqlite3* db = nullptr;
	};
}
