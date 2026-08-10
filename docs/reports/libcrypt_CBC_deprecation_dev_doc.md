# libcrypt CBC 模式缺陷分析与 AES-GCM 迁移 · 开发文档

> 项目：qing/micro_servers/libcrypt
> 模块：libcrypt（加密库）
> 状态：计划 v1.0
> 关联文档：[SECURITY_HARDENING_PLAN_20260803.md](../security/SECURITY_HARDENING_PLAN_20260803.md)

---

## 0. 摘要

libcrypt 当前对称加密类 `AES_CBC` 使用 AES-256-CBC 模式。CBC 模式存在若干**结构性安全缺陷**（padding oracle、无完整性、IV 重用、bit-flipping），在现代密码学实践中已不再推荐用于新系统。本文档分析这些缺陷在当前实现中的暴露面，并给出迁移至 AES-256-GCM（AEAD）的完整开发计划。

---

## 1. CBC 模式原理回顾

CBC（Cipher Block Chaining）将明文分块后，每块在加密前先与前一块密文 XOR：

```
C_0 = E_K(P_0 ⊕ IV)
C_i = E_K(P_i ⊕ C_{i-1})   (i ≥ 1)
```

解密：
```
P_0 = D_K(C_0) ⊕ IV
P_i = D_K(C_i) ⊕ C_{i-1}   (i ≥ 1)
```

- **优点**：相同明文在不同 IV/位置产生不同密文，模式隐藏了块级重复。
- **缺点**：仅提供机密性（confidentiality），不提供完整性（integrity）或真实性（authenticity）；加密无法并行；错误会传播到下一块。

---

## 2. CBC 的结构性缺陷

### 2.1 无完整性保护（No Authentication）

CBC 是**纯加密**模式，不产生任何认证标签（authentication tag）。密文被篡改后，解密端无法检测篡改，会产生"合法但错误"的明文。

**攻击向量 — Bit-flipping**：

CBC 解密时 `P_i = D_K(C_i) ⊕ C_{i-1}`。攻击者翻转 `C_{i-1}` 中的第 j 个比特，将**精确翻转** `P_i` 的第 j 个比特，且仅破坏 `P_{i-1}`（变成随机噪声）。如果攻击者知道明文结构，可以精准修改解密结果。

**对当前实现的影响**：

```
AES_CBC::decrypt(cipher_hex)  // 直接返回解密结果，无任何校验
```

如果 `cipher_hex` 在传输中被篡改，`decrypt` 会静默返回损坏数据。调用方（如 gateway Token 解析）若不额外校验，可能导致逻辑绕过。

### 2.2 Padding Oracle 攻击

CBC 需要将明文填充到块大小（AES 为 16 字节）。当前实现使用 OpenSSL 默认的 **PKCS#7 填充**。PKCS#7 在解密失败和填充校验失败时表现不同：

- **填充合法** → 解密成功，返回明文
- **填充非法** → `EVP_DecryptFinal_ex` 返回 0，当前实现通过 `CRYPTO_CHECK` 抛出异常

**攻击原理**（Vaudenay 2002）：

攻击者发送修改后的密文，观察服务端"解密成功"还是"填充错误"的差别（即 oracle）。每次只需 ~256 次尝试即可恢复一个明文字节，**完整明文可在 O(n × 256) 内恢复**，无需知道密钥。

**对当前实现的影响**：

```
CRYPTO_CHECK(EVP_DecryptFinal_ex(...) == 1, "解密结束失败");
//                         ↑ 失败时抛出 std::runtime_error
```

异常消息中"解密结束失败"与"解密更新失败"不同，攻击者可通过不同的错误响应区分填充校验阶段，构成 padding oracle。即便错误消息相同，**响应时间差异**也足以构成时序 oracle。

**历史案例**：POODLE（SSLv3）、Lucky13（TLS 1.1/1.2 CBC）、ASP.NET ViewState Padding Oracle。

### 2.3 IV 重用风险

当前 `AES_CBC` 的参数化构造函数接受外部 IV，且 **同一 IV 可被多次复用**：

```cpp
AES_CBC aes(ext_key, ext_iv);  // IV 固定在对象生命周期内
aes.encrypt("msg1");            // 使用固定 IV
aes.encrypt("msg2");            // 同一 IV 再次使用！
```

**攻击原理**：

如果同一 (key, IV) 加密两条消息 `P` 和 `P'`：
```
C_0  = E_K(P_0  ⊕ IV)
C'_0 = E_K(P'_0 ⊕ IV)
```

若 `P_0 == P'_0`，则 `C_0 == C'_0`。攻击者可通过比对密文首块判断明文是否相同。虽然后续块因链式 XOR 不会直接暴露，但首块信息泄漏已构成安全问题。

**对当前实现的影响**：

当前实现未禁止 IV 重用，也未在每次 `encrypt` 时随机化 IV。默认构造函数每次生成随机 IV，但参数化构造函数将 IV 固化在对象中。

### 2.4 不可并行

CBC 加密链式依赖前一块密文，无法并行化。对于大体积数据（如文件加密），性能不如 CTR/GCM 模式。解密虽可并行（只需前一块密文），但加密端始终串行。

### 2.5 BEAST 类攻击面

CBC 加密使用**可预测的 IV**（即前一条消息的最后一个密文块）时，易受选择明文攻击（BEAST, CVE-2011-3389）。当前实现使用随机 IV，但若调用方误用固定 IV，此风险即刻生效。

---

## 3. 当前实现缺陷评估

| 缺陷 | 当前暴露程度 | 严重性 | 说明 |
|------|-------------|--------|------|
| 无完整性保护 | **完全暴露** | 🔴 高 | `decrypt` 不校验密文完整性，直接返回明文 |
| Padding oracle | **部分暴露** | 🟠 中高 | 异常消息区分失败阶段；时序差异可被利用 |
| IV 重用 | **部分暴露** | 🟡 中 | 参数化构造函数固化 IV，可多次 encrypt |
| 不可并行 | 性能问题 | 🟢 低 | 当前无大体积加密场景 |
| BEAST | 条件性 | 🟢 低 | 随机 IV 缓解，但需禁止外部固定 IV |

---

## 4. 迁移目标：AES-256-GCM（AEAD）

### 4.1 为什么选 GCM

GCM（Galois/Counter Mode）是 **AEAD**（Authenticated Encryption with Associated Data）模式，同时提供：

- **机密性**：CTR 模式加密
- **完整性**：GHASH 多项式认证标签（16 字节 tag）
- **关联数据**（AEAD）：可对不加密的元数据（如 header）做认证

| 特性 | CBC | GCM |
|------|-----|-----|
| 机密性 | ✅ | ✅ |
| 完整性 | ❌ | ✅ (16B tag) |
| 认证元数据 | ❌ | ✅ (AAD) |
| 并行加密 | ❌ | ✅ |
| Padding | 需要 (PKCS#7) | 不需要 (流模式) |
| Padding oracle | 脆弱 | 不适用 |
| IV/Nonce 重用 | 泄漏明文 | **灾难性**（需严格管理） |
| 推荐度 | ⛔ 不推荐新系统 | ✅ NIST SP 800-38D 推荐 |

### 4.2 GCM 的关键约束

**Nonce（IV）绝不能在同一密钥下重用**。GCM nonce 重用比 CBC IV 重用严重得多——会导致认证密钥泄漏，攻击者可伪造任意密文。因此 nonce 管理是迁移的核心设计点。

- **推荐方案**：每次加密生成随机 12 字节 nonce（96-bit），OpenSSL 内部计数器模式安全
- **备选方案**：确定性 nonce（如序号），需保证单调递增且持久化

---

## 5. 开发计划

### 5.1 新增类 `AES_GCM`

**文件**：`inc/us/AES_GCM.hpp`（header-only，与现有风格一致）

**API 设计**：

```cpp
namespace qing {
    class AES_GCM : public qing::Crypto_Basic {
    public:
        // 默认构造：随机生成 32B 密钥
        AES_GCM();

        // 外部密钥（校验 32 字节）
        explicit AES_GCM(const std::vector<unsigned char>& key);

        // 加密：每次随机 nonce，返回 hex(nonce || ciphertext || tag)
        std::string encrypt(const std::string& plaintext,
                            const std::string& aad = "");

        // 解密：解析 hex 输入，校验 tag，失败抛异常
        std::string decrypt(const std::string& cipher_hex,
                            const std::string& aad = "");

        // 密钥管理
        std::string get_key() const;

    private:
        std::vector<unsigned char> key;

        static constexpr size_t NONCE_LEN = 12;   // 96-bit nonce
        static constexpr size_t TAG_LEN   = 16;   // 128-bit tag
    };
}
```

**输出格式**：

```
encrypt("hello", "header") → hex(nonce[12] || ciphertext[n] || tag[16])
```

将 nonce 和 tag 与密文拼接为一个 blob，调用方无需单独管理 nonce。解密时从 blob 中切分出 nonce、ciphertext、tag 三段。

**核心实现要点**：

```cpp
// 加密
EVP_CIPHER_CTX_ptr ctx(EVP_CIPHER_CTX_new());
EVP_EncryptInit_ex(ctx.get(), EVP_aes_256_gcm(), nullptr, nullptr, nullptr);
EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_IVLEN, NONCE_LEN, nullptr);

// 生成随机 nonce
std::vector<unsigned char> nonce(NONCE_LEN);
RAND_bytes(nonce.data(), NONCE_LEN);

EVP_EncryptInit_ex(ctx.get(), nullptr, nullptr, key.data(), nonce.data());

// 注入 AAD（可选）
if (!aad.empty())
    EVP_EncryptUpdate(ctx.get(), nullptr, &len, aad_data, aad_len);

// 加密明文
EVP_EncryptUpdate(ctx.get(), ciphertext.data(), &len, plaintext, plain_len);

// 获取 tag
EVP_EncryptFinal_ex(ctx.get(), ..., &len);
EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_GET_TAG, TAG_LEN, tag.data());

// 解密时校验 tag
EVP_CIPHER_CTX_ctrl(ctx.get(), EVP_CTRL_GCM_SET_TAG, TAG_LEN, tag.data());
// EVP_DecryptFinal_ex 返回 0 → 认证失败，必须拒绝
```

### 5.2 废弃 `AES_CBC`

- 将 `AES_CBC` 标记为 `[[deprecated("Use AES_GCM instead. CBC lacks integrity protection.")]]`
- 保留现有 API 不变，编译时产生 `-Wdeprecated-declarations` 警告
- 在 README.md 中标注废弃说明
- **不删除** `AES_CBC`，保证存量代码编译通过，给予迁移过渡期

### 5.3 迁移步骤

| 步骤 | 内容 | 优先级 | 预估 |
|------|------|--------|------|
| S1 | 实现 `AES_GCM` 类 + 单元测试 | P0 | 0.5 天 |
| S2 | 标记 `AES_CBC` 为 deprecated | P0 | 0.1 天 |
| S3 | 更新 Makefile install 目标（新增 AES_GCM.hpp） | P0 | 0.1 天 |
| S4 | 排查所有 `AES_CBC` 使用方 | P1 | 0.5 天 |
| S5 | 逐模块迁移至 `AES_GCM` | P1 | 1 天/模块 |
| S6 | 全量回归测试 | P1 | 1 天 |
| S7 | 下个大版本移除 `AES_CBC` | P2 | — |

### 5.4 使用方排查

当前 `AES_CBC` 的使用方：

```bash
# 搜索所有引用
grep -r "AES_CBC" --include="*.cpp" --include="*.hpp" --include="*.h" .
```

| 模块 | 使用方式 | 迁移建议 |
|------|---------|---------|
| gateway | Token 签发使用 RSA，密码使用 PBKDF2，未直接使用 AES_CBC | 无需迁移 |
| 其他模块 | 搜索确认 | 按需迁移 |

### 5.5 Makefile 变更

```makefile
# install 目标新增
@cp -vf $(INCLU_SDIR)/$(FRAMEWORK_NAME)/AES_GCM.hpp $(INCLU_TDIR)/$(FRAMEWORK_NAME)/
# uninstall 对应新增
@rm -vf $(INCLU_TDIR)/$(FRAMEWORK_NAME)/AES_GCM.hpp
```

---

## 6. 安全注意事项

### 6.1 Nonce 管理铁律

> **同一密钥下，nonce 绝不能重用。**

GCM nonce 重用的后果：
- 两条消息用同一 (key, nonce) → 认证密钥 H 可被恢复
- 攻击者获取 H 后可伪造任意密文，且无法检测

**对策**：
- `encrypt` 内部每次随机生成 nonce（`RAND_bytes`），禁止外部传入
- 使用 96-bit nonce（NIST 推荐长度），随机碰撞概率：\(2^{-48}\) 次才会碰撞（单密钥 \(2^{32}\) 次加密内安全）

### 6.2 Tag 校验必须先于明文使用

解密流程必须 **先验证 tag，再使用明文**。当前 `AES_CBC::decrypt` 直接返回明文，GCM 版本必须：

```cpp
// 正确流程
EVP_DecryptUpdate(ctx, plaintext, &len, ciphertext, ct_len);  // 先解密
int ret = EVP_DecryptFinal_ex(ctx, ...);                       // 再验 tag
if (ret != 1) throw CryptoExp("认证失败");                     // 失败则拒绝
return plaintext;                                               // 通过才返回
```

**错误模式**（不可采用）：
- 返回明文给调用方后再校验 tag
- 校验失败时已泄漏明文（时间窗口）

### 6.3 AAD（关联数据）的使用场景

AAD 允许对**不加密但需认证**的数据做完整性校验。建议在以下场景使用：

| 场景 | AAD 内容 | 效果 |
|------|---------|------|
| 服务间通信帧 | 帧头（源/目标/序号/时间戳） | 防篡改帧头 |
| Token | Token 版本号 + 签发者标识 | 防跨版本重放 |
| 文件加密 | 文件名 + 修改时间 | 防元数据篡改 |

---

## 7. 测试计划

### 7.1 功能测试

| 测试项 | 方法 |
|--------|------|
| 加解密往返 | encrypt → decrypt == 原文 |
| AAD 认证 | encrypt(aad="x") → decrypt(aad="x") 成功；decrypt(aad="y") 失败 |
| Tag 篡改 | 翻转密文末尾 1 bit → decrypt 抛异常 |
| Nonce 唯一性 | 连续 encrypt 1000 次，nonce 各不相同 |
| 空明文 | encrypt("") → decrypt("") 成功 |
| 大数据 | encrypt(1MB) → decrypt == 原文 |

### 7.2 兼容性测试

| 测试项 | 方法 |
|--------|------|
| 与 OpenSSL CLI 互通 | `openssl enc -aes-256-gcm` 生成 → C++ 解密 |
| RFC 测试向量 | 使用 NIST GCM 测试向量验证正确性 |

### 7.3 性能测试

| 场景 | 指标 |
|------|------|
| 1KB 加密 | ops/sec |
| 1MB 加密 | MB/sec |
| 与 CBC 对比 | 加密吞吐量比值 |

---

## 8. 文档更新

完成开发后需同步更新：

- [ ] `libcrypt/README.md` — 新增 AES_GCM 章节，标注 AES_CBC 废弃
- [ ] `docs/` 根目录或 `docs/reports/` 中登记本文档
- [ ] `libcrypt/Makefile` — install/uninstall 新增 AES_GCM.hpp

---

## 9. 里程碑

| 里程碑 | 内容 | 预估 |
|--------|------|------|
| M1 | S1-S3：AES_GCM 实现 + 测试 + 废弃标记 | 1 天 |
| M2 | S4-S5：使用方排查 + 迁移 | 1-2 天 |
| M3 | S6-S7：回归测试 + 文档更新 | 1 天 |

---

## 10. 参考资料

- NIST SP 800-38D — GCM 和 GMAC 规范
- RFC 5116 — AEAD 接口
- Vaudenay, "Security Flaws Induced by CBC Padding" (Eurocrypt 2002)
- OpenSSL EVP AEAD API 文档
- OWASP Cryptographic Storage Cheat Sheet
