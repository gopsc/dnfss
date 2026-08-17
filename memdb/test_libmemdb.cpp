/*
 * test_libmemdb.cpp — libmemdb 模板类单元测试
 *
 * 编译: g++ -std=c++20 -O0 -g -Wall -Werror -I ../libmemdb/inc -o test_libmemdb test_libmemdb.cpp
 * 运行: ./test_libmemdb
 */

#include <us/memdb.hpp>

#include <cassert>
#include <chrono>
#include <iostream>
#include <optional>
#include <string>
#include <thread>
#include <vector>

static int g_passed = 0;
static int g_failed = 0;

#define TEST(name)                                                        \
    do {                                                                  \
        std::cout << "  " << (name) << " ... ";                           \
    } while (0)

#define PASS()                                                            \
    do {                                                                  \
        std::cout << "PASS" << std::endl;                                 \
        ++g_passed;                                                       \
    } while (0)

#define FAIL(msg)                                                         \
    do {                                                                  \
        std::cout << "FAIL: " << (msg) << std::endl;                      \
        ++g_failed;                                                       \
    } while (0)

#define CHECK(cond, msg)                                                  \
    do {                                                                  \
        if (!(cond)) { FAIL(msg); return; }                               \
    } while (0)

// ── insert ──────────────────────────────────────────────────────────────

static void test_insert_basic() {
    TEST("insert basic");
    Memdb<std::string, std::string> db;
    CHECK(db.insert("a", "1"), "insert new key should succeed");
    CHECK(db.size() == 1, "size should be 1");
    CHECK(db.search("a").has_value(), "search should find key");
    CHECK(db.search("a").value() == "1", "value should match");
    PASS();
}

static void test_insert_duplicate() {
    TEST("insert duplicate");
    Memdb<std::string, std::string> db;
    db.insert("a", "1");
    CHECK(!db.insert("a", "2"), "insert duplicate should fail");
    CHECK(db.search("a").value() == "1", "value should be unchanged");
    CHECK(db.size() == 1, "size should still be 1");
    PASS();
}

static void test_insert_int_keys() {
    TEST("insert int keys");
    Memdb<int, std::string> db;
    CHECK(db.insert(1, "one"), "");
    CHECK(db.insert(2, "two"), "");
    CHECK(db.search(1).value() == "one", "");
    CHECK(db.search(2).value() == "two", "");
    CHECK(db.size() == 2, "");
    PASS();
}

// ── remove ──────────────────────────────────────────────────────────────

static void test_remove_existing() {
    TEST("remove existing");
    Memdb<std::string, std::string> db;
    db.insert("a", "1");
    db.insert("b", "2");
    CHECK(db.remove("a"), "remove existing should succeed");
    CHECK(!db.search("a").has_value(), "removed key should not be found");
    CHECK(db.size() == 1, "size should be 1");
    CHECK(db.search("b").value() == "2", "other key should still exist");
    PASS();
}

static void test_remove_nonexistent() {
    TEST("remove nonexistent");
    Memdb<std::string, std::string> db;
    CHECK(!db.remove("ghost"), "remove nonexistent should fail");
    CHECK(db.size() == 0, "size should still be 0");
    PASS();
}

static void test_remove_all() {
    TEST("remove all");
    Memdb<std::string, std::string> db;
    db.insert("a", "1");
    db.insert("b", "2");
    db.insert("c", "3");
    db.remove("a");
    db.remove("b");
    db.remove("c");
    CHECK(db.size() == 0, "size should be 0");
    CHECK(!db.search("a").has_value(), "");
    CHECK(!db.search("b").has_value(), "");
    CHECK(!db.search("c").has_value(), "");
    PASS();
}

// ── update ──────────────────────────────────────────────────────────────

static void test_update_without_refresh() {
    TEST("update without refresh TTL");
    Memdb<std::string, std::string> db(1);  // 1s TTL
    db.insert("a", "1");
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    CHECK(db.update("a", "2", false), "update should succeed");
    CHECK(db.search("a").value() == "2", "value should be updated");
    // 500ms left — should still exist
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    CHECK(db.search("a").has_value(), "should still exist (800ms < 1s)");
    PASS();
}

static void test_update_with_refresh() {
    TEST("update with refresh TTL");
    Memdb<std::string, std::string> db(1);  // 1s TTL
    db.insert("a", "1");
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    CHECK(db.update("a", "2", true), "update should succeed");
    CHECK(db.search("a").value() == "2", "value should be updated");
    // TTL was refreshed — should still exist
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    CHECK(db.search("a").has_value(), "should still exist after refresh");
    PASS();
}

static void test_update_nonexistent() {
    TEST("update nonexistent");
    Memdb<std::string, std::string> db;
    CHECK(!db.update("ghost", "1"), "update nonexistent should fail");
    PASS();
}

// ── search ──────────────────────────────────────────────────────────────

static void test_search_nonexistent() {
    TEST("search nonexistent");
    Memdb<std::string, std::string> db;
    CHECK(!db.search("ghost").has_value(), "search nonexistent should return nullopt");
    PASS();
}

// ── size ────────────────────────────────────────────────────────────────

static void test_size_empty() {
    TEST("size empty");
    Memdb<std::string, std::string> db;
    CHECK(db.size() == 0, "empty db should have size 0");
    PASS();
}

static void test_size_after_ops() {
    TEST("size after operations");
    Memdb<std::string, std::string> db;
    db.insert("a", "1");
    db.insert("b", "2");
    CHECK(db.size() == 2, "size after 2 inserts");
    db.remove("a");
    CHECK(db.size() == 1, "size after remove");
    db.insert("c", "3");
    CHECK(db.size() == 2, "size after re-insert");
    PASS();
}

// ── TTL expiration (flush) ──────────────────────────────────────────────

static void test_ttl_expiration() {
    TEST("TTL expiration");
    Memdb<std::string, std::string> db(1);  // 1s TTL
    db.insert("a", "1");
    db.insert("b", "2");
    CHECK(db.size() == 2, "initial size 2");

    std::this_thread::sleep_for(std::chrono::milliseconds(1100));

    // search triggers flush
    CHECK(!db.search("a").has_value(), "a should be expired");
    CHECK(!db.search("b").has_value(), "b should be expired");
    CHECK(db.size() == 0, "size should be 0 after flush");
    PASS();
}

static void test_ttl_partial_expiration() {
    TEST("TTL partial expiration");
    Memdb<std::string, std::string> db(1);
    db.insert("a", "1");
    std::this_thread::sleep_for(std::chrono::milliseconds(600));
    db.insert("b", "2");  // fresher
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    // a: 1100ms old → expired, b: 500ms old → alive
    CHECK(!db.search("a").has_value(), "a should be expired");
    CHECK(db.search("b").has_value(), "b should still be alive");
    CHECK(db.size() == 1, "size should be 1");
    PASS();
}

static void test_ttl_zero_means_immediate() {
    TEST("TTL zero means immediate expiration");
    Memdb<std::string, std::string> db(0);
    db.insert("a", "1");
    CHECK(!db.search("a").has_value(), "zero TTL should expire immediately");
    CHECK(db.size() == 0, "size should be 0");
    PASS();
}

// ── get_db / show ───────────────────────────────────────────────────────

static void test_get_db() {
    TEST("get_db returns all entries");
    Memdb<std::string, std::string> db(10);
    db.insert("x", "100");
    db.insert("y", "200");
    const auto& lst = db.get_db();
    CHECK(lst.size() == 2, "list should have 2 entries");
    // verify values are accessible
    bool found_x = false, found_y = false;
    for (const auto& col : lst) {
        if (col.key == "x" && col.value == "100") found_x = true;
        if (col.key == "y" && col.value == "200") found_y = true;
    }
    CHECK(found_x && found_y, "both entries should be in the list");
    PASS();
}

// ── get_keys ────────────────────────────────────────────────────────────

static void test_get_keys() {
    TEST("get_keys returns key index");
    Memdb<std::string, std::string> db;
    db.insert("k1", "v1");
    const auto& keys = db.get_keys();
    CHECK(keys.size() == 1, "keys should have 1 entry");
    CHECK(keys.find("k1") != keys.end(), "k1 should be in keys");
    PASS();
}

// ── get_time_touch ──────────────────────────────────────────────────────

static void test_get_time_touch() {
    TEST("get_time_touch returns sorted timestamps");
    Memdb<std::string, std::string> db(10);
    db.insert("a", "1");
    db.insert("b", "2");
    const auto& tt = db.get_time_touch();
    CHECK(tt.size() == 2, "time_touch should have 2 entries");
    // verify sorted order (a before b since a was inserted first)
    auto it = tt.begin();
    auto first = it;
    ++it;
    CHECK(first->item->timestamp <= it->item->timestamp,
          "time_touch should be sorted by timestamp");
    PASS();
}

// ── copy semantics ──────────────────────────────────────────────────────

static void test_copy_assignment() {
    TEST("copy assignment");
    Memdb<std::string, std::string> db2;
    {
        Memdb<std::string, std::string> db1;
        db1.insert("a", "1");
        db2 = db1;
        CHECK(db2.search("a").value() == "1", "copied db should have same data");
        // verify independence
        db2.insert("b", "2");
        CHECK(db1.size() == 1, "original should be unchanged");
        CHECK(db2.size() == 2, "copy should be independent");
    }
    // db1 destroyed — db2 must still work (indices must not dangle)
    CHECK(db2.search("a").value() == "1", "copy must survive source destruction");
    CHECK(db2.remove("a"), "copy remove should work after source destroyed");
    CHECK(db2.size() == 1, "copy size after remove");
    PASS();
}

static void test_copy_constructor() {
    TEST("copy constructor");
    std::optional<Memdb<std::string, std::string>> cpy;
    {
        Memdb<std::string, std::string> src;
        src.insert("a", "1");
        src.insert("b", "2");
        cpy.emplace(src);  // copy constructor
        CHECK(cpy->size() == 2, "copy should have 2 entries");
        CHECK(src.size() == 2, "source unchanged");
    }
    CHECK(cpy->search("a").value() == "1", "copy value a after source destroyed");
    CHECK(cpy->search("b").value() == "2", "copy value b after source destroyed");
    cpy->remove("a");
    CHECK(cpy->size() == 1, "copy mutation after source destroyed");
    // flush on copy must not touch freed memory
    CHECK(cpy->search("b").value() == "2", "flush path safe on copy");
    PASS();
}

static void test_update_refresh_extends_ttl() {
    TEST("update with refresh extends TTL");
    Memdb<std::string, std::string> db(1);
    db.insert("a", "1");
    std::this_thread::sleep_for(std::chrono::milliseconds(700));
    CHECK(db.update("a", "2", true), "update with refresh should succeed");
    std::this_thread::sleep_for(std::chrono::milliseconds(600));
    CHECK(db.search("a").value() == "2",
          "entry alive past original TTL thanks to refresh");
    PASS();
}

// ── edge cases ──────────────────────────────────────────────────────────

static void test_large_number_of_keys() {
    TEST("large number of keys");
    Memdb<int, int> db(60);
    const int N = 1000;
    for (int i = 0; i < N; ++i) {
        CHECK(db.insert(i, i * 10), "all inserts should succeed");
    }
    CHECK(db.size() == static_cast<size_t>(N), "size should be N");
    for (int i = 0; i < N; ++i) {
        CHECK(db.search(i).value() == i * 10, "all values should match");
    }
    PASS();
}

static void test_reinsert_after_remove() {
    TEST("reinsert after remove");
    Memdb<std::string, std::string> db;
    db.insert("a", "1");
    db.remove("a");
    CHECK(db.insert("a", "2"), "reinsert should succeed");
    CHECK(db.search("a").value() == "2", "value should be updated");
    PASS();
}

static void test_update_without_refresh_keeps_old_ttl() {
    TEST("update without refresh keeps old TTL");
    Memdb<std::string, std::string> db(1);
    db.insert("a", "1");
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    db.update("a", "2", false);  // value changes, TTL unchanged
    // wait remaining 200ms+ — should expire
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    CHECK(!db.search("a").has_value(), "should expire at original TTL");
    PASS();
}

// ────────────────────────────────────────────────────────────────────────

int main() {
    std::cout << "=== libmemdb unit tests ===" << std::endl;

    test_insert_basic();
    test_insert_duplicate();
    test_insert_int_keys();
    test_remove_existing();
    test_remove_nonexistent();
    test_remove_all();
    test_update_without_refresh();
    test_update_with_refresh();
    test_update_nonexistent();
    test_search_nonexistent();
    test_size_empty();
    test_size_after_ops();
    test_ttl_expiration();
    test_ttl_partial_expiration();
    test_ttl_zero_means_immediate();
    test_get_db();
    test_get_keys();
    test_get_time_touch();
    test_copy_assignment();
    test_copy_constructor();
    test_update_refresh_extends_ttl();
    test_large_number_of_keys();
    test_reinsert_after_remove();
    test_update_without_refresh_keeps_old_ttl();

    std::cout << "\n=== Results: " << g_passed << " passed, "
              << g_failed << " failed ===" << std::endl;
    return g_failed > 0 ? 1 : 0;
}