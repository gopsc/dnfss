#pragma once
#include <iostream>
#include <exception>
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/ini_parser.hpp>
namespace property_tree = boost::property_tree;
using ptree = property_tree::ptree;
namespace qing {

	class ConfIni {
	public:
		ConfIni(const std::string &filename) {
			try {
				std::cout << "open and parse ini file: " << filename << std::endl;
				property_tree::ini_parser::read_ini(filename, pt);
			}
			catch (const boost::property_tree::ini_parser_error& e) {
				std::cerr << "INI parsing error: " << e.what() << std::endl;
				throw IniConfigureFileParseException("INI parsing error!");
			}
		}

		template <typename T>
		T get(const std::string &path, const T &_defaut) {
			return pt.get<T>(path, _defaut);
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
