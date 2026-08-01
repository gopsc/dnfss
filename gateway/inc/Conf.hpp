#pragma once
#include <iostream>
#include <exception>
#include <string>
#include <sstream>
#include <cstdlib>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/ini_parser.hpp>
namespace property_tree = boost::property_tree;
using ptree = property_tree::ptree;
namespace qing {

	class ConfIni {
	public:
		/// 默认构造（空配置，后续通过赋值覆盖，仅供 confs 加载流程内部使用）
		ConfIni() = default;

		/// 从文件路径解析 ini（保留用于开发/调试模式）
		ConfIni(const std::string &filename) {
			if (filename.empty()) return;  // 空文件名不报错，后续会通过赋值覆盖
			try {
				std::cout << "[conf] 从文件加载配置: " << filename << std::endl;
				property_tree::ini_parser::read_ini(filename, pt);
			}
			catch (const boost::property_tree::ini_parser_error& e) {
				std::cerr << "[conf] INI 解析错误: " << e.what() << std::endl;
				throw IniConfigureFileParseException("INI parsing error!");
			}
		}

		/// 从 ini 文本字符串解析（用于从 confs 拉取后解析）
		static ConfIni fromString(const std::string &ini_text) {
			ConfIni c;
			try {
				std::istringstream iss(ini_text);
				property_tree::ini_parser::read_ini(iss, c.pt);
			}
			catch (const boost::property_tree::ini_parser_error& e) {
				std::cerr << "[conf] INI 文本解析错误: " << e.what() << std::endl;
				throw IniConfigureFileParseException("INI parsing error!");
			}
			return c;
		}

		template <typename T>
		T get(const std::string &path, const T &_defaut) {
			return pt.get<T>(path, _defaut);
		}

		/// 读取环境变量，不存在则返回默认值
		static std::string env(const char *name, const std::string &default_val = "") {
			const char *v = std::getenv(name);
			return v ? std::string(v) : default_val;
		}

		static int env_int(const char *name, int default_val) {
			const char *v = std::getenv(name);
			if (!v || !*v) return default_val;
			try { return std::stoi(v); }
			catch (...) { return default_val; }
		}

		class IniConfigureFileParseException: public std::exception {
		public:
			explicit IniConfigureFileParseException(const std::string &msg)
				: message(msg) {}
			const char* what() const noexcept override {
				return message.c_str();
			}
		private:
			std::string message;
		};

	private:
		ptree pt;
	};
}
