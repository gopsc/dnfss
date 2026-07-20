#pragma once
#include "Crypto_Basic.hpp"
#include <iostream>
#include <fstream>
#include <vector>
#include <memory>
#include <stdexcept>
#include <sstream>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/err.h>
#include <openssl/core_names.h>
#include <openssl/rsa.h>

// 错误处理宏（打印完整错误栈，便于调试）
#define HANDLE_SSL_ERROR() { \
    ERR_print_errors_fp(stderr); \
}

// 自定义删除器用于管理OpenSSL资源
struct EVP_PKEY_deleter {
    void operator()(EVP_PKEY* pkey) const { EVP_PKEY_free(pkey); }
};
using EVP_PKEY_ptr = std::unique_ptr<EVP_PKEY, EVP_PKEY_deleter>;

struct EVP_PKEY_CTX_deleter {
    void operator()(EVP_PKEY_CTX* ctx) const { EVP_PKEY_CTX_free(ctx); }
};
using EVP_PKEY_CTX_ptr = std::unique_ptr<EVP_PKEY_CTX, EVP_PKEY_CTX_deleter>;

struct BIO_deleter {
    void operator()(BIO* bio) const { BIO_free_all(bio); }
};
using BIO_ptr = std::unique_ptr<BIO, BIO_deleter>;

struct EVP_MD_CTX_deleter {
    void operator()(EVP_MD_CTX* ctx) const { EVP_MD_CTX_free(ctx); }
};
using EVP_MD_CTX_ptr = std::unique_ptr<EVP_MD_CTX, EVP_MD_CTX_deleter>;

namespace qing {
    // 签名填充模式枚举
    enum class RSASignaturePadding {
        PKCS1_v1_5,  // 传统 PKCS#1 v1.5 填充（兼容旧系统）
        PSS          // 概率签名方案（更安全，推荐）
    };

    class MyRSA : public qing::Crypto_Basic {
    public:
        class Generator {
        public:
            Generator() {
                key = generate_rsa_key();
            }

            // 保存私钥为 PKCS#8 格式（AES-256-CBC 加密），支持密码保护
            void save_pri_key(const std::string& filename, const char* password = nullptr) {
                if (!save_private_key(key.get(), filename, password)) {
                    throw Crypto_Basic::CryptoExp("保存私钥失败。");
                }
            }

            // 保存公钥（现代 SubjectPublicKeyInfo 格式）
            void save_pub_key(const std::string& filename) {
                if (!save_public_key(key.get(), filename)) {
                    throw Crypto_Basic::CryptoExp("保存公钥失败。");
                }
            }

            // 获取密钥（直接使用）
            EVP_PKEY* get_private_key() const { return key.get(); }

            // 导出为 PEM 字符串（PKCS#8 格式）
            std::string get_private_key_pem(const char* password = nullptr) {
                return key_to_pem(key.get(), true, password);
            }

            std::string get_public_key_pem() {
                return key_to_pem(key.get(), false);
            }

        private:
            EVP_PKEY_ptr key;

            static EVP_PKEY_ptr generate_rsa_key(size_t key_bits = 2048) {
                EVP_PKEY_CTX_ptr ctx(EVP_PKEY_CTX_new_from_name(nullptr, "RSA", nullptr));
                if (!ctx) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }
                if (EVP_PKEY_keygen_init(ctx.get()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }

                OSSL_PARAM params[] = {
                    OSSL_PARAM_construct_size_t(OSSL_PKEY_PARAM_RSA_BITS, &key_bits),
                    OSSL_PARAM_END
                };

                if (EVP_PKEY_CTX_set_params(ctx.get(), params) <= 0) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }

                EVP_PKEY* pkey = nullptr;
                if (EVP_PKEY_generate(ctx.get(), &pkey) <= 0) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }

                return EVP_PKEY_ptr(pkey);
            }

            // 保存私钥为 PKCS#8 加密格式
            static bool save_private_key(EVP_PKEY* pkey, const std::string& filename,
                                         const char* password = nullptr) {
                BIO_ptr bio(BIO_new_file(filename.c_str(), "wb"));
                if (!bio) {
                    std::cerr << "Failed to open file: " << filename << std::endl;
                    return false;
                }

                const EVP_CIPHER* cipher = password ? EVP_aes_256_cbc() : nullptr;
                int ret = PEM_write_bio_PKCS8PrivateKey(bio.get(), pkey, cipher,
                                                        (char*)password,
                                                        password ? static_cast<int>(strlen(password)) : 0,
                                                        nullptr, nullptr);
                if (ret != 1) {
                    HANDLE_SSL_ERROR();
                    return false;
                }
                return true;
            }

            static bool save_public_key(EVP_PKEY* pkey, const std::string& filename) {
                BIO_ptr bio(BIO_new_file(filename.c_str(), "wb"));
                if (!bio) {
                    std::cerr << "Failed to open file: " << filename << std::endl;
                    return false;
                }

                int ret = PEM_write_bio_PUBKEY(bio.get(), pkey);
                if (ret != 1) {
                    HANDLE_SSL_ERROR();
                    return false;
                }
                return true;
            }

            static std::string key_to_pem(EVP_PKEY* pkey, bool is_private,
                                          const char* password = nullptr) {
                BIO_ptr bio(BIO_new(BIO_s_mem()));
                if (!bio) {
                    HANDLE_SSL_ERROR();
                    return "";
                }

                int ret;
                if (is_private) {
                    const EVP_CIPHER* cipher = password ? EVP_aes_256_cbc() : nullptr;
                    ret = PEM_write_bio_PKCS8PrivateKey(bio.get(), pkey, cipher,
                                                        (char*)password,
                                                        password ? static_cast<int>(strlen(password)) : 0,
                                                        nullptr, nullptr);
                } else {
                    ret = PEM_write_bio_PUBKEY(bio.get(), pkey);
                }

                if (ret != 1) {
                    HANDLE_SSL_ERROR();
                    return "";
                }

                char* data;
                long len = BIO_get_mem_data(bio.get(), &data);
                return std::string(data, len);
            }
        };

        // 私钥类：支持 PKCS#1 和 PKCS#8 格式（含加密）
        class Private_Key : Crypto_Basic {
        public:
            // 从文件加载（支持密码）
            Private_Key(const std::string& fullpath, bool from_file = true,
                        const char* password = nullptr) {
                if (from_file) {
                    if (!(pri_key = load_private_key_from_file(fullpath, password)))
                        throw Crypto_Basic::CryptoExp("读取私钥失败");
                } else {
                    if (!(pri_key = load_private_key_from_string(fullpath, password)))
                        throw Crypto_Basic::CryptoExp("从字符串加载私钥失败");
                }
            }

            static Private_Key from_pem(const std::string& pem_string,
                                        const char* password = nullptr) {
                return Private_Key(pem_string, false, password);
            }

            // 解密（OAEP）
            std::string Decrypt(const std::string& cipher_hex) {
                std::vector<unsigned char> ciphertext = hex_to_bytes(cipher_hex);
                std::vector<unsigned char> decrypted = rsa_decrypt(pri_key.get(), ciphertext);
                if (decrypted.empty()) throw Crypto_Basic::CryptoExp("解密失败");
                return std::string(decrypted.begin(), decrypted.end());
            }

            // 签名（支持 PSS 和 PKCS#1 v1.5）
            std::string Sign(const std::string& message,
                             RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> msg(message.begin(), message.end());
                std::vector<unsigned char> signature = rsa_sign(pri_key.get(), msg, padding);
                if (signature.empty()) throw Crypto_Basic::CryptoExp("签名失败");
                return bytes_to_hex(signature);
            }

            std::vector<unsigned char> SignBytes(const std::string& message,
                                                 RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> msg(message.begin(), message.end());
                return rsa_sign(pri_key.get(), msg, padding);
            }

            std::string Sign(const std::vector<unsigned char>& message,
                             RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> signature = rsa_sign(pri_key.get(), message, padding);
                if (signature.empty()) throw Crypto_Basic::CryptoExp("签名失败");
                return bytes_to_hex(signature);
            }

            std::vector<unsigned char> SignBytes(const std::vector<unsigned char>& message,
                                                 RSASignaturePadding padding = RSASignaturePadding::PSS) {
                return rsa_sign(pri_key.get(), message, padding);
            }

            EVP_PKEY* get() const { return pri_key.get(); }

        private:
            EVP_PKEY_ptr pri_key;

            static EVP_PKEY_ptr load_private_key_from_file(const std::string& filename,
                                                           const char* password = nullptr) {
                BIO_ptr bio(BIO_new_file(filename.c_str(), "rb"));
                if (!bio) {
                    std::cerr << "Failed to open file: " << filename << std::endl;
                    return nullptr;
                }

                pem_password_cb* cb = nullptr;
                if (password) {
                    cb = [](char* buf, int size, int rwflag, void* u) -> int {
                        if (!u) return 0;
                        const char* pass = (const char*)u;
                        int len = strlen(pass);
                        if (len > size) len = size;
                        memcpy(buf, pass, len);
                        return len;
                    };
                }

                EVP_PKEY* pkey = PEM_read_bio_PrivateKey(bio.get(), nullptr, cb, (void*)password);
                if (!pkey) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }
                return EVP_PKEY_ptr(pkey);
            }

            static EVP_PKEY_ptr load_private_key_from_string(const std::string& pem_string,
                                                             const char* password = nullptr) {
                BIO_ptr bio(BIO_new_mem_buf(pem_string.c_str(), -1));
                if (!bio) {
                    std::cerr << "Failed to create BIO from string" << std::endl;
                    return nullptr;
                }

                pem_password_cb* cb = nullptr;
                if (password) {
                    cb = [](char* buf, int size, int rwflag, void* u) -> int {
                        if (!u) return 0;
                        const char* pass = (const char*)u;
                        int len = strlen(pass);
                        if (len > size) len = size;
                        memcpy(buf, pass, len);
                        return len;
                    };
                }

                EVP_PKEY* pkey = PEM_read_bio_PrivateKey(bio.get(), nullptr, cb, (void*)password);
                if (!pkey) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }
                return EVP_PKEY_ptr(pkey);
            }

            // RSA 解密（OAEP）
            static std::vector<unsigned char> rsa_decrypt(EVP_PKEY* private_key,
                                                           const std::vector<unsigned char>& ciphertext) {
                EVP_PKEY_CTX_ptr ctx(EVP_PKEY_CTX_new(private_key, nullptr));
                if (!ctx) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                if (EVP_PKEY_decrypt_init(ctx.get()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                if (EVP_PKEY_CTX_set_rsa_padding(ctx.get(), RSA_PKCS1_OAEP_PADDING) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                size_t outlen = 0;
                if (EVP_PKEY_decrypt(ctx.get(), nullptr, &outlen,
                                     ciphertext.data(), ciphertext.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                std::vector<unsigned char> plaintext(outlen);
                if (EVP_PKEY_decrypt(ctx.get(), plaintext.data(), &outlen,
                                     ciphertext.data(), ciphertext.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                plaintext.resize(outlen);
                return plaintext;
            }

            // RSA 签名（修正了 PSS 参数顺序）
            static std::vector<unsigned char> rsa_sign(EVP_PKEY* private_key,
                                                       const std::vector<unsigned char>& message,
                                                       RSASignaturePadding padding) {
                EVP_MD_CTX_ptr ctx(EVP_MD_CTX_new());
                if (!ctx) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                const EVP_MD* md = EVP_sha256();

                if (EVP_DigestSignInit(ctx.get(), nullptr, md, nullptr, private_key) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                EVP_PKEY_CTX* pkey_ctx = EVP_MD_CTX_get_pkey_ctx(ctx.get());
                if (!pkey_ctx) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                int padding_mode;
                if (padding == RSASignaturePadding::PKCS1_v1_5) {
                    padding_mode = RSA_PKCS1_PADDING;
                } else {
                    padding_mode = RSA_PKCS1_PSS_PADDING;
                }

                // 先设置填充模式（关键）
                if (EVP_PKEY_CTX_set_rsa_padding(pkey_ctx, padding_mode) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                // 如果是 PSS，再设置盐长度
                if (padding == RSASignaturePadding::PSS) {
                    if (EVP_PKEY_CTX_set_rsa_pss_saltlen(pkey_ctx, -1) <= 0) {
                        HANDLE_SSL_ERROR();
                        return {};
                    }
                }

                size_t siglen = 0;
                if (EVP_DigestSign(ctx.get(), nullptr, &siglen,
                                   message.data(), message.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                std::vector<unsigned char> signature(siglen);
                if (EVP_DigestSign(ctx.get(), signature.data(), &siglen,
                                   message.data(), message.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                signature.resize(siglen);
                return signature;
            }
        };

        // 公钥类
        class Public_Key : public Crypto_Basic {
        public:
            Public_Key(const std::string& filename, bool from_file = true) {
                if (from_file) {
                    if (!(pub_key = load_public_key_from_file(filename)))
                        throw Crypto_Basic::CryptoExp("读取公钥失败");
                } else {
                    if (!(pub_key = load_public_key_from_string(filename)))
                        throw Crypto_Basic::CryptoExp("从字符串加载公钥失败");
                }
            }

            static Public_Key from_pem(const std::string& pem_string) {
                return Public_Key(pem_string, false);
            }

            // 加密（OAEP）
            std::string Encrypt(const std::string& original_text) {
                std::vector<unsigned char> plaintext(original_text.begin(), original_text.end());
                std::vector<unsigned char> ciphertext = rsa_encrypt(pub_key.get(), plaintext);
                if (ciphertext.empty()) throw Crypto_Basic::CryptoExp("加密失败");
                return bytes_to_hex(ciphertext);
            }

            // 验证签名（支持 PSS 和 PKCS#1 v1.5）
            bool Verify(const std::string& message, const std::string& signature_hex,
                        RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> msg(message.begin(), message.end());
                std::vector<unsigned char> signature = hex_to_bytes(signature_hex);
                return rsa_verify(pub_key.get(), msg, signature, padding);
            }

            bool Verify(const std::string& message, const std::vector<unsigned char>& signature,
                        RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> msg(message.begin(), message.end());
                return rsa_verify(pub_key.get(), msg, signature, padding);
            }

            bool Verify(const std::vector<unsigned char>& message, const std::string& signature_hex,
                        RSASignaturePadding padding = RSASignaturePadding::PSS) {
                std::vector<unsigned char> signature = hex_to_bytes(signature_hex);
                return rsa_verify(pub_key.get(), message, signature, padding);
            }

            bool Verify(const std::vector<unsigned char>& message,
                        const std::vector<unsigned char>& signature,
                        RSASignaturePadding padding = RSASignaturePadding::PSS) {
                return rsa_verify(pub_key.get(), message, signature, padding);
            }

            EVP_PKEY* get() const { return pub_key.get(); }

        private:
            EVP_PKEY_ptr pub_key;

            static EVP_PKEY_ptr load_public_key_from_file(const std::string& filename) {
                BIO_ptr bio(BIO_new_file(filename.c_str(), "rb"));
                if (!bio) {
                    std::cerr << "Failed to open file: " << filename << std::endl;
                    return nullptr;
                }

                EVP_PKEY* pkey = PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr);
                if (!pkey) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }
                return EVP_PKEY_ptr(pkey);
            }

            static EVP_PKEY_ptr load_public_key_from_string(const std::string& pem_string) {
                BIO_ptr bio(BIO_new_mem_buf(pem_string.c_str(), -1));
                if (!bio) {
                    std::cerr << "Failed to create BIO from string" << std::endl;
                    return nullptr;
                }

                EVP_PKEY* pkey = PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr);
                if (!pkey) {
                    HANDLE_SSL_ERROR();
                    return nullptr;
                }
                return EVP_PKEY_ptr(pkey);
            }

            static std::vector<unsigned char> rsa_encrypt(EVP_PKEY* public_key,
                                                          const std::vector<unsigned char>& plaintext) {
                EVP_PKEY_CTX_ptr ctx(EVP_PKEY_CTX_new(public_key, nullptr));
                if (!ctx) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                if (EVP_PKEY_encrypt_init(ctx.get()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                if (EVP_PKEY_CTX_set_rsa_padding(ctx.get(), RSA_PKCS1_OAEP_PADDING) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                size_t outlen = 0;
                if (EVP_PKEY_encrypt(ctx.get(), nullptr, &outlen,
                                     plaintext.data(), plaintext.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                std::vector<unsigned char> ciphertext(outlen);
                if (EVP_PKEY_encrypt(ctx.get(), ciphertext.data(), &outlen,
                                     plaintext.data(), plaintext.size()) <= 0) {
                    HANDLE_SSL_ERROR();
                    return {};
                }

                ciphertext.resize(outlen);
                return ciphertext;
            }

            // RSA 验证（修正了 PSS 参数顺序）
            static bool rsa_verify(EVP_PKEY* public_key,
                                   const std::vector<unsigned char>& message,
                                   const std::vector<unsigned char>& signature,
                                   RSASignaturePadding padding) {
                EVP_MD_CTX_ptr ctx(EVP_MD_CTX_new());
                if (!ctx) {
                    HANDLE_SSL_ERROR();
                    return false;
                }

                const EVP_MD* md = EVP_sha256();

                if (EVP_DigestVerifyInit(ctx.get(), nullptr, md, nullptr, public_key) <= 0) {
                    HANDLE_SSL_ERROR();
                    return false;
                }

                EVP_PKEY_CTX* pkey_ctx = EVP_MD_CTX_get_pkey_ctx(ctx.get());
                if (!pkey_ctx) {
                    HANDLE_SSL_ERROR();
                    return false;
                }

                int padding_mode;
                if (padding == RSASignaturePadding::PKCS1_v1_5) {
                    padding_mode = RSA_PKCS1_PADDING;
                } else {
                    padding_mode = RSA_PKCS1_PSS_PADDING;
                }

                // 先设置填充模式（关键）
                if (EVP_PKEY_CTX_set_rsa_padding(pkey_ctx, padding_mode) <= 0) {
                    HANDLE_SSL_ERROR();
                    return false;
                }

                // 如果是 PSS，再设置盐长度
                if (padding == RSASignaturePadding::PSS) {
                    if (EVP_PKEY_CTX_set_rsa_pss_saltlen(pkey_ctx, -1) <= 0) {
                        HANDLE_SSL_ERROR();
                        return false;
                    }
                }

                int result = EVP_DigestVerify(ctx.get(), signature.data(), signature.size(),
                                              message.data(), message.size());

                if (result == 1) {
                    return true;
                } else if (result == 0) {
                    return false;
                } else {
                    HANDLE_SSL_ERROR();
                    return false;
                }
            }
        };
    };
} // namespace qing
