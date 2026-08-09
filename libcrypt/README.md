# libcrypt — UniServe 加密库

C++20 轻量级加密库，基于 OpenSSL 3.x EVP API 封装。提供 AES-256-CBC 加解密、Base64 编解码、SHA-256 哈希、RSA 密钥管理与加解密/签名验证。

命名空间 `qing`，库文件 `libus_crypt.so`，头文件安装至 `/usr/include/us/`。被 `gateway` 的 Token 签发/验证和密码哈希直接依赖。

## 架构

```
qing::Crypto_Basic          (基类: bytes_to_hex / hex_to_bytes / CRYPTO_CHECK)
├── AES_CBC                 AES-256-CBC 对称加密（随机密钥+IV / 外部密钥）
├── Base64                  Base64 编解码（OpenSSL BIO 链）
├── SHA256                  SHA-256 哈希与验证
└── MyRSA                   RSA 非对称密钥
    ├── Generator           密钥对生成（2048-bit，PKCS#8 PEM 输出）
    ├── Private_Key         私钥加载、OAEP 解密、PKCS1_v1_5/PSS 签名
    └── Public_Key          公钥加载、OAEP 加密、PKCS1_v1_5/PSS 验签
```

所有加密类均使用 OpenSSL 3.x EVP 高层 API。`bytes_to_hex` / `hex_to_bytes` 在 `Crypto_Basic` 中实现（查表法），编译进 `libus_crypt.so`。加解密类为纯头文件（header-only），需链接时额外加 `-lssl -lcrypto`。

## 类与 API

### Crypto_Basic — 基类

提供两个静态工具方法和异常类型，不直接实例化。

| 成员 | 说明 |
|------|------|
| `bytes_to_hex(data)` | 字节向量 → 十六进制字符串（查表法） |
| `hex_to_bytes(hex)` | 十六进制字符串 → 字节向量（大小写兼容） |
| `CryptoExp` | 异常类，继承 `std::runtime_error` |
| `CRYPTO_CHECK(cond, msg)` | 宏，条件为 false 时抛出 `std::runtime_error` |

### AES_CBC — AES-256-CBC 对称加密

> ⚠️ **CBC 模式存在结构性安全缺陷**（无完整性保护、Padding Oracle、IV 重用风险）。详见 [CBC 迁移开发文档](../docs/reports/libcrypt_CBC_deprecation_dev_doc.md)。新代码建议使用 AES-GCM（待实现），现有 `AES_CBC` 保留以兼容存量代码。

```cpp
AES_CBC aes;                           // 自动生成随机 32B 密钥 + 16B IV
AES_CBC aes(key_vec, iv_vec);          // 使用外部密钥和 IV（校验 32B 密钥 + 16B IV）
std::string hex = aes.encrypt(text);   // 加密，返回 hex 密文
std::string raw = aes.decrypt(hex);    // 解密 hex 密文，返回原文
std::string k = aes.get_key();         // 获取密钥（hex）
std::string v = aes.get_iv();          // 获取 IV（hex）
```

加密输出为 hex 编码的密文，非原始字节。解密接受 hex 输入，返回原始字符串。

参数化构造函数会校验密钥长度（必须 32 字节）和 IV 长度（必须 16 字节），不匹配时抛出 `std::runtime_error`。

### Base64 — Base64 编解码

提供实例方法和静态工具方法两种调用方式。

| 方法 | 说明 |
|------|------|
| `encode(str)` / `encode(vec)` | 编码为 Base64 字符串 |
| `decode_to_string(b64)` | 解码为原始字符串 |
| `decode_to_bytes(b64)` | 解码为字节向量 |
| `base64_encode(str)` (static) | 静态编码工具 |
| `base64_decode_to_string(b64)` (static) | 静态解码工具 |
| `is_valid_base64(str)` (static) | 验证格式合法性：字符集 + 长度为 4 的倍数 + `=` 填充规则（不依赖 OpenSSL） |

编码不添加换行符（`BIO_FLAGS_BASE64_NO_NL`）。

### SHA256 — SHA-256 哈希

```cpp
SHA256 h;
std::string hex = h.hash("hello");           // 字符串 → hex 哈希
auto bytes = h.hash_bytes("hello");          // 字符串 → 字节哈希
bool ok = h.verify("hello", expected_hex);   // 验证（常量时间比较，防时序侧信道）

// 静态快捷方式
std::string h = SHA256::sha256("hello");
```

### MyRSA — RSA 非对称密钥

**Generator** — 生成 2048-bit RSA 密钥对（OpenSSL 3.x `OSSL_PARAM` API）。

```cpp
MyRSA::Generator gen;
gen.save_pri_key("private.pem", "password");  // PKCS#8 + AES-256-CBC 加密
gen.save_pub_key("public.pem");               // SubjectPublicKeyInfo
std::string pem = gen.get_public_key_pem();    // 导出 PEM 字符串（失败时抛出 CryptoExp）
```

**Private_Key** — 加载私钥（支持 PKCS#1/PKCS#8，加密或明文）。

```cpp
auto pri = MyRSA::Private_Key("private.pem", true, "password");
std::string raw = pri.Decrypt(cipher_hex);         // OAEP 解密
std::string sig = pri.Sign(msg);                   // PSS 签名（默认）
std::string sig = pri.Sign(msg, RSASignaturePadding::PKCS1_v1_5);
```

> 私有继承自 `Crypto_Basic`，内部使用其工具方法，外部不可将 `Private_Key*` 转为 `Crypto_Basic*`。

**Public_Key** — 加载公钥。

```cpp
auto pub = MyRSA::Public_Key("public.pem");
std::string hex = pub.Encrypt(text);               // OAEP 加密
bool ok = pub.Verify(msg, sig_hex);                // 验签（默认 PSS）
bool ok = pub.Verify(msg, sig_hex, RSASignaturePadding::PKCS1_v1_5);
```

**签名填充模式**（`RSASignaturePadding` 枚举）：

| 值 | 说明 |
|----|------|
| `PSS` | 概率签名方案（默认，更安全） |
| `PKCS1_v1_5` | 传统填充（兼容旧系统） |

## 构建与安装

**前置依赖**：OpenSSL 3.x（开发包 `libssl-dev`），g++ C++20。

```bash
# 一键构建 + 安装（需 sudo）
bash _set.sh

# 或手动
make            # 编译 libus_crypt.so
make install    # 安装头文件到 /usr/include/us/ + .so 到 /lib/
make clear      # 清理构建产物
make uninstall  # 卸载
```

使用：

```cpp
#include <us/Crypto_Basic.hpp>
#include <us/AES_CBC.hpp>
#include <us/Base64.hpp>
#include <us/SHA256.hpp>
#include <us/MyRSA.hpp>
```

链接（OpenSSL 符号在 header-only 类中，需显式链接）：

```bash
g++ -std=c++20 ... -lus_crypt -lssl -lcrypto
```

## 使用示例

```cpp
#include <us/SHA256.hpp>
#include <us/Base64.hpp>
#include <us/AES_CBC.hpp>
#include <iostream>

int main() {
    // SHA-256
    std::string hash = qing::SHA256::sha256("hello world");
    std::cout << "SHA256: " << hash << std::endl;

    // Base64
    std::string b64 = qing::Base64::base64_encode("hello");
    std::cout << "Base64: " << b64 << std::endl;

    // AES-256-CBC
    qing::AES_CBC aes;
    std::string cipher = aes.encrypt("secret message");
    std::string plain = aes.decrypt(cipher);
    std::cout << "AES decrypt: " << plain << std::endl;

    return 0;
}
```

## 项目结构

```
libcrypt/
├── inc/us/
│   ├── Crypto_Basic.hpp   # 基类：hex 转换 + 异常
│   ├── AES_CBC.hpp        # AES-256-CBC 加解密（header-only）
│   ├── Base64.hpp         # Base64 编解码（header-only）
│   ├── SHA256.hpp         # SHA-256 哈希（header-only）
│   └── MyRSA.hpp          # RSA 密钥/加解密/签名（header-only）
├── src/
│   └── Crypto_Basic.cpp   # bytes_to_hex / hex_to_bytes 实现
├── Makefile
├── _set.sh
└── README.md
```

## 注意事项

- **OpenSSL 版本**：MyRSA 的 `Generator` 使用了 `OSSL_PARAM`、`EVP_PKEY_CTX_new_from_name` 等 OpenSSL 3.x 独占 API，无法在 OpenSSL 1.1.x 下编译。AES/Base64/SHA256 兼容 1.1.x。
- **链接要求**：`libus_crypt.so` 仅含 `bytes_to_hex` 和 `hex_to_bytes`（无 OpenSSL 调用）。所有加密类为 header-only，链接时必须添加 `-lssl -lcrypto`。
- **RSA 密钥长度**：默认 2048-bit，可在 `generate_rsa_key(size_t key_bits)` 中调整。
- **AES 密钥和 IV**：默认构造函数随机生成，每次创建 `AES_CBC` 对象产生不同的密钥对。需要长期存储密钥时使用参数化构造函数传入外部密钥。
- **hex 编码**：`bytes_to_hex` 使用查表法实现，位于 `Crypto_Basic.cpp` 中编译进 `.so`。`SHA256` 不再有私有副本。
- **`Private_Key` 私有继承**：该设计防止 `Private_Key` 被隐式转换为 `Crypto_Basic*`，是刻意为之的安全措施。

## 变更记录

### v0.0.2 — 安全加固（2026-08-08）

| 文件 | 变更 |
|------|------|
| AES_CBC.hpp | 添加 `#pragma once`；`EVP_CIPHER_CTX` 改为 RAII（`EVP_CIPHER_CTX_ptr`），消除异常路径内存泄漏；构造函数校验密钥 32B / IV 16B；参数改为 `const&`；`encrypt`/`decrypt` 参数改为 `const std::string&`；`EVP_EncryptUpdate` 入参 `static_cast<int>` 防 size_t 截断；移除 `print_bytes` 调试方法 |
| SHA256.hpp | `EVP_MD_CTX` 改为 RAII（`EVP_MD_CTX_ptr_sha`）；`verify()` 改用 `CRYPTO_memcmp` 常量时间比较，防时序侧信道攻击；移除 `print_hash` 调试方法 |
| Base64.hpp | 添加 `#pragma once`；`BIO*` 改为 RAII（`BIO_ptr_b64`）；`is_valid_base64` 增强校验（长度为 4 的倍数 + `=` 仅末尾且 ≤2 个）；移除 `print_encoded`/`print_decoded` 调试方法 |
| MyRSA.hpp | `Generator` 构造函数校验密钥非空，失败抛出 `CryptoExp`；`key_to_pem` 失败时抛出异常而非返回空字符串；提取共享 `password_callback` 静态方法，消除两处重复 lambda |

## 许可证

MIT License
