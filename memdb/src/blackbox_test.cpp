#include <iostream>
#include <chrono>
#include <thread>
#include <string>
#include <optional>
#include <functional>
#include <vector>
#include <utility>

// 使用 C++20 的时间字面量
using namespace std::chrono_literals;
using namespace shufeng2012;

#include "main/memdb.cpp"

// ----- 简易测试框架 -----
static int g_test_passed = 0;
static int g_test_failed = 0;

#define TEST(name) \
    void test_##name(); \
    struct TestReg_##name { \
        TestReg_##name() { register_test(#name, test_##name); } \
    } reg_##name; \
    void test_##name()

using TestFunc = std::function<void()>;
std::vector<std::pair<std::string, TestFunc>> tests;

void register_test(const std::string& name, TestFunc fn) {
    tests.push_back({name, fn});
}

// 断言宏（支持 C++20 的 constexpr 字符串，但这里不变）
#define EXPECT_TRUE(expr) \
    do { if (!(expr)) { std::cerr << "  [FAIL] " #expr << " is false\n"; ++g_test_failed; return; } } while(0)

#define EXPECT_FALSE(expr) EXPECT_TRUE(!(expr))

#define EXPECT_EQ(expected, actual) \
    do { if ((expected) != (actual)) { std::cerr << "  [FAIL] Expected: " << (expected) << ", Actual: " << (actual) << "\n"; ++g_test_failed; return; } } while(0)

#define EXPECT_OPTIONAL_EQ(expected, opt) \
    do { \
        if (!(opt).has_value()) { std::cerr << "  [FAIL] optional has no value\n"; ++g_test_failed; return; } \
        if ((expected) != (opt).value()) { std::cerr << "  [FAIL] Expected: " << (expected) << ", Actual: " << (opt).value() << "\n"; ++g_test_failed; return; } \
    } while(0)

#define EXPECT_NO_VALUE(opt) \
    do { if ((opt).has_value()) { std::cerr << "  [FAIL] optional should have no value, but has: " << (opt).value() << "\n"; ++g_test_failed; return; } } while(0)

// ----- 测试用例 -----

// 1. 默认构造
TEST(DefaultConstruction) {
    Memdb<int, std::string> db;
    EXPECT_EQ(0u, db.size());
    EXPECT_TRUE(db.get_db().empty());
    EXPECT_TRUE(db.get_keys().empty());
    EXPECT_TRUE(db.get_time_touch().empty());
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 2. 插入与查询
TEST(InsertAndSearch) {
    Memdb<int, std::string> db(10);
    EXPECT_TRUE(db.insert(1, "one"));
    EXPECT_EQ(1u, db.size());
    auto opt = db.search(1);
    EXPECT_OPTIONAL_EQ("one", opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 3. 重复插入失败
TEST(InsertDuplicateFails) {
    Memdb<int, std::string> db;
    EXPECT_TRUE(db.insert(1, "one"));
    EXPECT_FALSE(db.insert(1, "another"));
    EXPECT_EQ(1u, db.size());
    auto opt = db.search(1);
    EXPECT_OPTIONAL_EQ("one", opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 4. 查询不存在的键
TEST(SearchNonExistent) {
    Memdb<int, std::string> db;
    auto opt = db.search(42);
    EXPECT_NO_VALUE(opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 5. 删除存在的键
TEST(RemoveExisting) {
    Memdb<int, std::string> db;
    db.insert(1, "one");
    EXPECT_TRUE(db.remove(1));
    EXPECT_EQ(0u, db.size());
    auto opt = db.search(1);
    EXPECT_NO_VALUE(opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 6. 删除不存在的键
TEST(RemoveNonExistentFails) {
    Memdb<int, std::string> db;
    EXPECT_FALSE(db.remove(99));
    EXPECT_EQ(0u, db.size());
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 7. 删除后重新插入
TEST(RemoveThenReinsert) {
    Memdb<int, std::string> db;
    db.insert(1, "one");
    db.remove(1);
    EXPECT_TRUE(db.insert(1, "new"));
    EXPECT_EQ(1u, db.size());
    auto opt = db.search(1);
    EXPECT_OPTIONAL_EQ("new", opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 8. 更新（不触摸）
TEST(UpdateWithoutTouch) {
    Memdb<int, std::string> db(10);
    db.insert(1, "one");
    EXPECT_TRUE(db.update(1, "updated", false));
    EXPECT_EQ(1u, db.size());
    auto opt = db.search(1);
    EXPECT_OPTIONAL_EQ("updated", opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 9. 更新（触摸）延长寿命 —— 使用 C++20 字面量
TEST(UpdateWithTouchExtendsLifetime) {
    const int TTL = 3;
    Memdb<int, std::string> db(TTL);
    db.insert(1, "one");

    // 等待 1.5 秒（剩余寿命约 1.5 秒）
    std::this_thread::sleep_for(1500ms);

    // 触摸更新，重置寿命为从当前起 TTL 秒
    EXPECT_TRUE(db.update(1, "touched", true));

    // 再等待 2.5 秒（从触摸起 2.5 < TTL，应存活）
    std::this_thread::sleep_for(2500ms);

    db.search(999);   // 触发 flush
    auto opt = db.search(1);
    EXPECT_TRUE(opt.has_value());
    EXPECT_OPTIONAL_EQ("touched", opt);
    EXPECT_EQ(1u, db.size());

    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 10. 更新不存在的键
TEST(UpdateNonExistentFails) {
    Memdb<int, std::string> db;
    EXPECT_FALSE(db.update(2, "nope"));
    EXPECT_EQ(0u, db.size());
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 11. 过期后自动清除
TEST(ExpirationAfterTimeout) {
    const int TTL = 2;
    Memdb<int, std::string> db(TTL);
    db.insert(1, "expire");

    // 等待 TTL + 1 秒（保证超过）
    std::this_thread::sleep_for(std::chrono::seconds(TTL + 1));

    db.search(999);   // 触发 flush
    EXPECT_EQ(0u, db.size());
    EXPECT_NO_VALUE(db.search(1));

    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 12. 未过期前可查询
TEST(NotExpiredBeforeTimeout) {
    const int TTL = 2;
    Memdb<int, std::string> db(TTL);
    db.insert(1, "alive");
    std::this_thread::sleep_for(1s);
    EXPECT_EQ(1u, db.size());
    auto opt = db.search(1);
    EXPECT_OPTIONAL_EQ("alive", opt);
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 13. 更新不触摸不延长寿命
TEST(UpdateWithoutTouchDoesNotExtend) {
    const int TTL = 3;
    Memdb<int, std::string> db(TTL);
    db.insert(1, "one");

    // 等待 1.5 秒（剩余 1.5 秒）
    std::this_thread::sleep_for(1500ms);

    // 更新但不触摸，过期时间不变
    EXPECT_TRUE(db.update(1, "updated", false));

    // 再等待 2 秒（总休眠 3.5 > TTL，应过期）
    std::this_thread::sleep_for(2000ms);

    db.search(999);
    EXPECT_EQ(0u, db.size());
    EXPECT_NO_VALUE(db.search(1));

    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 14. 生存时间为 0，立即过期
TEST(ZeroLifetimeExpiresImmediately) {
    Memdb<int, std::string> db(0);
    db.insert(1, "zero");
    std::this_thread::sleep_for(10ms);
    db.search(999);
    EXPECT_EQ(0u, db.size());
    EXPECT_NO_VALUE(db.search(1));
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 15. 混合操作一致性
TEST(MixedInsertDeleteSize) {
    Memdb<int, std::string> db;
    db.insert(1, "a");
    db.insert(2, "b");
    db.insert(3, "c");
    db.remove(2);
    db.insert(4, "d");
    EXPECT_EQ(3u, db.size());
    EXPECT_TRUE(db.search(1).has_value());
    EXPECT_FALSE(db.search(2).has_value());
    EXPECT_TRUE(db.search(3).has_value());
    EXPECT_TRUE(db.search(4).has_value());
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// 16. 批量过期
TEST(BulkExpirationClearsSize) {
    const int TTL = 1;
    Memdb<int, std::string> db(TTL);
    for (int i = 0; i < 100; ++i) {
        db.insert(i, std::to_string(i));
    }
    EXPECT_EQ(100u, db.size());
    std::this_thread::sleep_for(std::chrono::seconds(TTL + 1));
    db.search(-1);
    EXPECT_EQ(0u, db.size());
    for (int i = 0; i < 100; ++i) {
        EXPECT_FALSE(db.search(i).has_value());
    }
    std::cout << "  [PASS]\n";
    ++g_test_passed;
}

// ----- 程序入口 -----
int main() {
    std::cout << "Memdb Black-box Tests (C++20, no external libs)\n";
    std::cout << "================================================\n";

    for (auto& [name, fn] : tests) {
        std::cout << "Test: " << name << "\n";
        fn();
    }

    std::cout << "================================================\n";
    std::cout << "Passed: " << g_test_passed << ", Failed: " << g_test_failed << "\n";

    return g_test_failed > 0 ? 1 : 0;
}