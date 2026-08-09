#pragma once
#include <openssl/evp.h>
#include <openssl/err.h>
#include <openssl/crypto.h>
#include <memory>
#include <vector>
#include <string>
#include <stdexcept>
#include "Crypto_Basic.hpp"

struct EVP_MD_CTX_deleter_sha {
	void operator()(EVP_MD_CTX* ctx) const { EVP_MD_CTX_free(ctx); }
};
using EVP_MD_CTX_ptr_sha = std::unique_ptr<EVP_MD_CTX, EVP_MD_CTX_deleter_sha>;

namespace qing {

	class SHA256: public qing::Crypto_Basic {
	
	public:
		SHA256() = default;

		/* 计算字符串的SHA-256哈希值，返回十六进制字符串 */
		std::string hash(const std::string& input_text) {
			std::vector<unsigned char> input(input_text.begin(), input_text.end());
			std::vector<unsigned char> digest = sha256_hash(input);
			return bytes_to_hex(digest);
		}

		/* 计算字节向量的SHA-256哈希值，返回十六进制字符串 */
		std::string hash(const std::vector<unsigned char>& input_bytes) {
			std::vector<unsigned char> digest = sha256_hash(input_bytes);
			return bytes_to_hex(digest);
		}

		/* 计算字符串的SHA-256哈希值，返回字节向量 */
		std::vector<unsigned char> hash_bytes(const std::string& input_text) {
			std::vector<unsigned char> input(input_text.begin(), input_text.end());
			return sha256_hash(input);
		}

		/* 计算字节向量的SHA-256哈希值，返回字节向量 */
		std::vector<unsigned char> hash_bytes(const std::vector<unsigned char>& input_bytes) {
			return sha256_hash(input_bytes);
		}

		/* 验证数据是否与给定的哈希值匹配 */
		bool verify(const std::string& input_text, const std::string& expected_hash_hex) {
			std::vector<unsigned char> computed = hash_bytes(input_text);
			std::vector<unsigned char> expected = hex_to_bytes(expected_hash_hex);
			if (computed.size() != expected.size()) return false;
			return CRYPTO_memcmp(computed.data(), expected.data(), computed.size()) == 0;
		}

		/* 验证数据是否与给定的哈希值匹配（字节向量版本） */
		bool verify(const std::vector<unsigned char>& input_bytes, const std::string& expected_hash_hex) {
			std::vector<unsigned char> computed = sha256_hash(input_bytes);
			std::vector<unsigned char> expected = hex_to_bytes(expected_hash_hex);
			if (computed.size() != expected.size()) return false;
			return CRYPTO_memcmp(computed.data(), expected.data(), computed.size()) == 0;
		}

		/* 静态方法：直接计算SHA-256哈希（工具函数） */
		static std::string sha256(const std::string& input_text) {
			SHA256 hasher;
			return hasher.hash(input_text);
		}

		/* 静态方法：直接计算SHA-256哈希，返回字节向量 */
		static std::vector<unsigned char> sha256_bytes(const std::string& input_text) {
			SHA256 hasher;
			return hasher.hash_bytes(input_text);
		}


	private:
		/* SHA-256哈希核心实现 */
		static std::vector<unsigned char> sha256_hash(const std::vector<unsigned char>& input) {
			// 创建EVP上下文（RAII 自动释放）
			EVP_MD_CTX_ptr_sha ctx(EVP_MD_CTX_new());
			CRYPTO_CHECK(ctx != nullptr, "无法创建哈希上下文");

			// 初始化SHA-256操作
			CRYPTO_CHECK(EVP_DigestInit_ex(ctx.get(), EVP_sha256(), nullptr) == 1,
						"SHA-256初始化失败");

			// 提供输入数据
			CRYPTO_CHECK(EVP_DigestUpdate(ctx.get(), input.data(), input.size()) == 1,
						"SHA-256更新失败");

			// 获取哈希结果
			std::vector<unsigned char> digest(EVP_MD_CTX_size(ctx.get())); // SHA-256输出32字节
			unsigned int digest_len = 0;
			CRYPTO_CHECK(EVP_DigestFinal_ex(ctx.get(), digest.data(), &digest_len) == 1,
						"SHA-256完成失败");

			// 调整到实际长度（应该是32字节）
			digest.resize(digest_len);

			return digest;
		}
	};
}
