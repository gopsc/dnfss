#pragma once
#include <openssl/evp.h>
#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <memory>
#include <vector>
#include <string>
#include <stdexcept>
#include "Crypto_Basic.hpp"

struct BIO_deleter_b64 {
	void operator()(BIO* bio) const { BIO_free_all(bio); }
};
using BIO_ptr_b64 = std::unique_ptr<BIO, BIO_deleter_b64>;

namespace qing {
	class Base64 : public qing::Crypto_Basic {
	public:
		/* 默认构造函数 */
		Base64() = default;
		
		/* 编码：将字符串编码为Base64 */
		std::string encode(const std::string& input_text) {
			return base64_encode(input_text);
		}
		
		/* 编码：将字节向量编码为Base64 */
		std::string encode(const std::vector<unsigned char>& input_bytes) {
			return base64_encode(input_bytes);
		}
		
		/* 解码：将Base64字符串解码为原始字符串 */
		std::string decode_to_string(const std::string& base64_text) {
			std::vector<unsigned char> decoded = base64_decode_to_bytes(base64_text);
			return std::string(decoded.begin(), decoded.end());
		}
		
		/* 解码：将Base64字符串解码为字节向量 */
		std::vector<unsigned char> decode_to_bytes(const std::string& base64_text) {
			return base64_decode_to_bytes(base64_text);
		}
		
		/* 静态方法：直接编码字符串（工具函数） */
		static std::string base64_encode(const std::string& input_text) {
			std::vector<unsigned char> input(input_text.begin(), input_text.end());
			return base64_encode(input);
		}
		
		/* 静态方法：直接编码字节向量（工具函数） */
		static std::string base64_encode(const std::vector<unsigned char>& input_bytes) {
			// 创建BIO链：内存BIO -> Base64 BIO（RAII 自动释放）
			BIO_ptr_b64 bio(BIO_new(BIO_s_mem()));
			CRYPTO_CHECK(bio != nullptr, "无法创建内存BIO");
			
			BIO* b64 = BIO_new(BIO_f_base64());
			CRYPTO_CHECK(b64 != nullptr, "无法创建Base64 BIO");
			
			// 将Base64 BIO连接到内存BIO
			bio.reset(BIO_push(b64, bio.release()));
			
			// 设置不添加换行符（默认会添加换行）
			BIO_set_flags(bio.get(), BIO_FLAGS_BASE64_NO_NL);
			
			// 写入数据
			int written = BIO_write(bio.get(), input_bytes.data(), static_cast<int>(input_bytes.size()));
			CRYPTO_CHECK(written == static_cast<int>(input_bytes.size()), "Base64编码写入失败");
			
			// 刷新缓冲区
			CRYPTO_CHECK(BIO_flush(bio.get()) == 1, "Base64编码刷新失败");
			
			// 读取编码结果
			BUF_MEM* bptr;
			BIO_get_mem_ptr(bio.get(), &bptr);
			
			// 复制结果到字符串（不包括空终止符）
			std::string result(bptr->data, bptr->length);
			
			return result;
		}
		
		/* 静态方法：直接解码为字符串（工具函数） */
		static std::string base64_decode_to_string(const std::string& base64_text) {
			std::vector<unsigned char> decoded = base64_decode_to_bytes(base64_text);
			return std::string(decoded.begin(), decoded.end());
		}
		
		/* 静态方法：直接解码为字节向量（工具函数） */
		static std::vector<unsigned char> base64_decode_to_bytes(const std::string& base64_text) {
			// 创建BIO链：Base64 BIO -> 内存BIO（RAII 自动释放）
			BIO_ptr_b64 bio(BIO_new_mem_buf(base64_text.c_str(), -1));
			CRYPTO_CHECK(bio != nullptr, "无法创建内存BIO");
			
			BIO* b64 = BIO_new(BIO_f_base64());
			CRYPTO_CHECK(b64 != nullptr, "无法创建Base64 BIO");
			
			// 将Base64 BIO连接到内存BIO
			bio.reset(BIO_push(b64, bio.release()));
			
			// 设置不添加换行符（解码时也要匹配）
			BIO_set_flags(bio.get(), BIO_FLAGS_BASE64_NO_NL);
			
			// 准备接收缓冲区
			std::vector<unsigned char> result;
			char buffer[4096];
			int bytes_read;
			
			// 循环读取解码数据
			while ((bytes_read = BIO_read(bio.get(), buffer, sizeof(buffer))) > 0) {
				result.insert(result.end(), buffer, buffer + bytes_read);
			}
			
			// 检查是否解码成功（BIO_read返回0表示结束，负值表示错误）
			CRYPTO_CHECK(bytes_read >= 0, "Base64解码失败");
			
			return result;
		}
		
		/* 验证Base64格式是否有效 */
		static bool is_valid_base64(const std::string& base64_text) {
			if (base64_text.empty()) return false;
			// 长度必须是4的倍数
			if (base64_text.size() % 4 != 0) return false;
			
			size_t padding = 0;
			for (size_t i = 0; i < base64_text.size(); ++i) {
				char c = base64_text[i];
				if (c == '=') {
					padding++;
					// '=' 只能出现在末尾且不超过2个
					if (padding > 2 || i < base64_text.size() - padding) return false;
				} else if (!((c >= 'A' && c <= 'Z') ||
				             (c >= 'a' && c <= 'z') ||
				             (c >= '0' && c <= '9') ||
				             c == '+' || c == '/')) {
					return false;
				}
			}
			return true;
		}

	};
}
