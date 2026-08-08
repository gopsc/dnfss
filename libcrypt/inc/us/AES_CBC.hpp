#pragma once
#include "Crypto_Basic.hpp"
#include <openssl/evp.h>
#include <openssl/err.h>
#include <openssl/rand.h>
#include <memory>
#include <vector>
#include <stdexcept>

struct EVP_CIPHER_CTX_deleter {
	void operator()(EVP_CIPHER_CTX* ctx) const { EVP_CIPHER_CTX_free(ctx); }
};
using EVP_CIPHER_CTX_ptr = std::unique_ptr<EVP_CIPHER_CTX, EVP_CIPHER_CTX_deleter>;

namespace qing {
	class AES_CBC: public qing::Crypto_Basic {
	public:
	/* 构造函数 初始化一个随即的密钥和初始化向量 */
	AES_CBC() {
		generate_key_iv(key, iv);
	}
	AES_CBC(const std::vector<unsigned char>& key, const std::vector<unsigned char>& iv) {
		CRYPTO_CHECK(key.size() == 32, "AES-256 需要 32 字节密钥");
		CRYPTO_CHECK(iv.size() == 16, "AES-CBC 需要 16 字节 IV");
		this->key = key;
		this->iv = iv;
	}
	std::string get_key() {
		return bytes_to_hex(key);
	}
	std::string get_iv() {
		return bytes_to_hex(iv);
	}
	
	std::string encrypt(const std::string& original_text) {
		std::vector<unsigned char> plaintext(original_text.begin(), original_text.end());
		std::vector<unsigned char> ciphertext = aes_encrypt(plaintext, key, iv);
		return bytes_to_hex(ciphertext);
	}

	std::string decrypt(const std::string& cipher_hex) {
		std::vector<unsigned char> ciphertext = hex_to_bytes(cipher_hex);
		std::vector<unsigned char> decrypted = aes_decrypt(ciphertext, key, iv);
		return std::string(decrypted.begin(), decrypted.end());
	}
	
	private:
	std::vector<unsigned char> key;
	std::vector<unsigned char> iv;
	// 生成随机密钥和IV
	static void generate_key_iv(std::vector<unsigned char>& key,
			std::vector<unsigned char>& iv) {
		key.resize(32);	// AES-256 需要 32 字节密钥
		iv.resize(16);	// AES 块大小 16 字节（128-bit）

		// 生成随即密钥
		CRYPTO_CHECK(RAND_bytes(key.data(), key.size()) == 1, "密钥生成失败");

		// 生成随即IV
		CRYPTO_CHECK(RAND_bytes(iv.data(), iv.size()) == 1, "IV生成失败");
	}

	// AES-256-CBC 加密
	static std::vector<unsigned char> aes_encrypt(const std::vector<unsigned char>& plaintext,
			const std::vector<unsigned char>& key,
			const std::vector<unsigned char>& iv) {
		// 创建上下文（RAII 自动释放）
		EVP_CIPHER_CTX_ptr ctx(EVP_CIPHER_CTX_new());
		CRYPTO_CHECK(ctx != nullptr, "无法创建加密上下文");

		// 初始化加密操作
		CRYPTO_CHECK(EVP_EncryptInit_ex(ctx.get(), EVP_aes_256_cbc(), nullptr,
					key.data(), iv.data()) == 1, "加密初始化失败");

		// 输出缓冲区 （密文可能比明文长一个块）
		std::vector<unsigned char> ciphertext(plaintext.size() + EVP_CIPHER_CTX_block_size(ctx.get()));
		int len = 0;
		int ciphertext_len = 0;

		// 提供明文数据
		CRYPTO_CHECK(EVP_EncryptUpdate(ctx.get(), ciphertext.data(), &len,
					plaintext.data(), static_cast<int>(plaintext.size())) == 1, "加密更新失败");
		ciphertext_len = len;

		// 完成加密（处理填充）
		CRYPTO_CHECK(EVP_EncryptFinal_ex(ctx.get(), ciphertext.data() + len, &len) == 1, "加密结束失败");
		ciphertext_len += len;

		// 调整到实际密文长度
		ciphertext.resize(ciphertext_len);

		return ciphertext;
	}

	// AES-256-CBC 解密
	static std::vector<unsigned char> aes_decrypt(const std::vector<unsigned char>& ciphertext,
			const std::vector<unsigned char>& key,
			const std::vector<unsigned char>& iv) {
		// 创建上下文（RAII 自动释放）
		EVP_CIPHER_CTX_ptr ctx(EVP_CIPHER_CTX_new());
		CRYPTO_CHECK(ctx != nullptr, "无法创建解密上下文");

		// 初始化解密操作
		CRYPTO_CHECK(EVP_DecryptInit_ex(ctx.get(), EVP_aes_256_cbc(), nullptr,
					key.data(), iv.data()) == 1, "解密初始化失败");

		// 输出缓冲区（明文可能比密文短，但最大为密文长度）
		std::vector<unsigned char> plaintext(ciphertext.size());
		int len = 0;
		int plaintext_len = 0;

		// 提供密文数据
		CRYPTO_CHECK(EVP_DecryptUpdate(ctx.get(), plaintext.data(), &len, 
					ciphertext.data(), static_cast<int>(ciphertext.size())) == 1, "解密更新失败");
		plaintext_len = len;

		// 完成解密（处理填充）
		CRYPTO_CHECK(EVP_DecryptFinal_ex(ctx.get(), plaintext.data() + len, &len) == 1, "解密结束失败");
		plaintext_len += len;

		// 调整到实际明文长度
		plaintext.resize(plaintext_len);

		return plaintext;
	}
	};
}
