#pragma once
#include <string>
#include <vector>
#include <stdexcept>

#define CRYPTO_CHECK(condition, message) \
	if (!(condition)) { \
		throw std::runtime_error(message); \
	}
namespace qing {
	class Crypto_Basic {
		public:
		class CryptoExp: public std::runtime_error {
			public:
			CryptoExp(const std::string& msg): std::runtime_error(msg) {}
		};
		protected:
		/* 字节码转十六进制字符串 */
		static std::string bytes_to_hex(const std::vector<unsigned char>& data);

		/* 十六进制字符串转字节码 */
		static std::vector<unsigned char> hex_to_bytes(const std::string &hex);
	};
}
