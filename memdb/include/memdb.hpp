#pragma once
#include <memory>
#include <list>
#include <unordered_map>
#include <chrono>
#include <queue>
#include <set>
#include <optional>



template <typename T, typename U>

class Memdb {


struct memdb_column {
    T key;
    U value;
    std::chrono::system_clock::time_point timestamp;
    bool be_del = false;
};

struct index_keys {
    T key;
    std::list <memdb_column>::iterator item;
};

struct index_timestamp {
    std::list <memdb_column>::iterator item;

    bool operator<(const index_timestamp& a) const;
};


private:
    std::list <memdb_column> db;
    std::unordered_map <T, index_keys> keys;
    std::priority_queue <index_timestamp> time_touch;
    std::multiset <index_timestamp> time_touch_;
    size_t __size = 0;
    int MAX_TIME = 60 * 10;       // 10分钟
    void flush();
    void update_queue();

    // 内测版
    void flush_();


public:
    using key_t = T;
    using value_t = U;
    static constexpr bool fail = false;
    static constexpr bool succ = true;

    Memdb();
    Memdb(int max_time);
    Memdb& operator=(const Memdb& to_be_cp_memdb);
    ~Memdb();

    size_t size() const;
    const std::unordered_map <T, index_keys>& get_keys() const;
    const std::list <memdb_column>& get_db() const;
    const std::priority_queue <index_timestamp>& get_time_touch() const;

    bool insert(key_t to_be_ins_k, value_t ins_v);
    bool remove(key_t to_be_del_k);
    bool update(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch=false);
    std::optional<U> search(key_t to_be_sch_k);

    // 内测版
    const std::multiset <index_timestamp>& get_time_touch_() const;
    bool insert_(key_t to_be_ins_k, value_t ins_v);
    bool remove_(key_t to_be_del_k);
    bool update_(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch=false);
    std::optional<U> search_(key_t to_be_sch_k);
};