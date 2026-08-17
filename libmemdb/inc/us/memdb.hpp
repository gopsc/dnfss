#pragma once
#include <list>
#include <unordered_map>
#include <chrono>
#include <set>
#include <optional>
#include <algorithm>
#include <utility>



template <typename T, typename U>

class Memdb {


struct memdb_column {
    T key;
    U value;
    std::chrono::system_clock::time_point timestamp;
};

struct index_keys {
    std::list <memdb_column>::iterator item;
};

struct index_timestamp {
    std::list <memdb_column>::iterator item;

    bool operator<(const index_timestamp& a) const {
        return this -> item -> timestamp < a.item -> timestamp;
    }
};


private:
    std::list <memdb_column> db;
    std::unordered_map <T, index_keys> keys;
    std::multiset <index_timestamp> time_touch;
    size_t __size = 0;
    int MAX_TIME = 60 * 10;       // 10分钟
    void flush() {
        if (this -> time_touch.empty()) {
            return;
        }
        std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
        auto it = this -> time_touch.begin() -> item;

        while (now_time >= it -> timestamp) {
            this -> __size--;
            key_t to_be_del_k = it -> key;
            this -> db.erase(it);
            this -> keys.erase(to_be_del_k);
            this -> time_touch.erase(this -> time_touch.begin());

            if (this -> time_touch.empty()) {
                return;
            }
            it = this -> time_touch.begin() -> item;
            now_time = std::chrono::system_clock::now();
        }
    }



public:
    using key_t = T;
    using value_t = U;
    static constexpr bool fail = false;
    static constexpr bool succ = true;



    explicit Memdb() = default;
    explicit Memdb(int max_time) : MAX_TIME(std::max(0, max_time)) {}

    // 深拷贝：重建索引，保证 keys/time_touch 的迭代器指向副本自身的 list
    Memdb(const Memdb& other)
        : __size(other.__size), MAX_TIME(other.MAX_TIME) {
        this -> db = other.db;
        for (auto it = this -> db.begin(); it != this -> db.end(); ++it) {
            Memdb::index_keys new_key;
            new_key.item = it;
            this -> keys.emplace(it -> key, new_key);

            Memdb::index_timestamp new_time_touch;
            new_time_touch.item = it;
            this -> time_touch.insert(new_time_touch);
        }
    }

    Memdb& operator=(const Memdb& other) {
        if (this == &other) {
            return *this;
        }
        Memdb tmp(other);
        std::swap(this -> db, tmp.db);
        std::swap(this -> keys, tmp.keys);
        std::swap(this -> time_touch, tmp.time_touch);
        std::swap(this -> __size, tmp.__size);
        std::swap(this -> MAX_TIME, tmp.MAX_TIME);
        return *this;
    }

    ~Memdb() = default;


    size_t size() {
        this -> flush();
        return this -> __size;
    }

    const std::unordered_map <T, index_keys>& get_keys() const {
        return this -> keys;
    }

    const std::list<memdb_column>& get_db() {
        this -> flush();
        return this -> db;
    }

    const std::multiset <typename Memdb <T, U>::index_timestamp>& get_time_touch() const {
        return this -> time_touch;
    }


    bool insert(key_t to_be_ins_k, value_t ins_v) {
        this -> flush();
        if (this -> keys.find(to_be_ins_k) != this -> keys.end()) {
            return this -> fail;
        }

        std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();

        Memdb::memdb_column new_column;
        new_column.key = to_be_ins_k;
        new_column.value = ins_v;
        new_column.timestamp = now_time + std::chrono::seconds(MAX_TIME);
        this -> db.push_back(new_column);

        Memdb::index_keys new_key;
        new_key.item = std::prev(this -> db.end());
        this -> keys.emplace(to_be_ins_k, new_key);

        Memdb::index_timestamp new_time_touch;
        new_time_touch.item = std::prev(this -> db.end());
        this -> time_touch.insert(new_time_touch);

        this -> __size++;

        return this -> succ;
    }

    bool remove(key_t to_be_del_k) {
        this -> flush();
        auto kit = this -> keys.find(to_be_del_k);
        if (kit == this -> keys.end()) {
            return this -> fail;
        }

        typename std::list <memdb_column>::iterator it = kit -> second.item;
        this -> keys.erase(kit);
        auto range = this -> time_touch.equal_range({it});
        for (auto sit = range.first; sit != range.second; ++sit) {
            if (sit -> item == it) {
                this -> time_touch.erase(sit);
                break;
            }
        }
        this -> db.erase(it);
        this -> __size--;

        return this -> succ;
    }

    bool update(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch=false) {
        this -> flush();
        auto kit = this -> keys.find(to_be_udt_k);
        if (kit == this -> keys.end()) {
            return this -> fail;
        }

        typename std::list <memdb_column>::iterator it = kit -> second.item;
        it -> value = udt_v;
        if (need_to_udt_timetouch == true) {
            auto range = this -> time_touch.equal_range({it});
            for (auto sit = range.first; sit != range.second; ++sit) {
                if (sit -> item == it) {
                    this -> time_touch.erase(sit);
                    break;
                }
            }
            it -> timestamp = std::chrono::system_clock::now()
                            + std::chrono::seconds(MAX_TIME);
            Memdb::index_timestamp new_time_touch;
            new_time_touch.item = it;
            this -> time_touch.insert(new_time_touch);
        }

        return this -> succ;
    }

    std::optional<U> search(key_t to_be_sch_k) {
        this -> flush();
        auto kit = this -> keys.find(to_be_sch_k);
        if (kit == this -> keys.end()) {
            return std::nullopt;
        }

        return kit -> second.item -> value;
    }
};