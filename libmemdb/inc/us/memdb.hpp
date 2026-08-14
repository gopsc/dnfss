#pragma once
#include <list>
#include <unordered_map>
#include <chrono>
#include <set>
#include <optional>
#include <cmath>



template <typename T, typename U>

class Memdb {


struct memdb_column {
    T key;
    U value;
    std::chrono::system_clock::time_point timestamp;
};

struct index_keys {
    T key;
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
    void flush();



public:
    using key_t = T;
    using value_t = U;
    static constexpr bool fail = false;
    static constexpr bool succ = true;



    explicit Memdb() = default;
    explicit Memdb(int max_time) : MAX_TIME(std::max(0, max_time)) {}
    explicit Memdb& operator=(const Memdb& to_be_cp_memdb) = default;
    explicit ~Memdb() = default;


    size_t size() const {
        return this -> __size;
    }

    const std::unordered_map <T, index_keys>& get_keys() const {
        return this -> keys;
    }

    const std::list <memdb_column>& get_db() const {
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
        new_key.key = to_be_ins_k;
        new_key.item = std::prev(this -> db.end());
        this -> keys[to_be_ins_k] = new_key;

        Memdb::index_timestamp new_time_touch;
        new_time_touch.item = std::prev(this -> db.end());
        this -> time_touch.insert(new_time_touch);

        this -> __size++;

        return this -> succ;
    }

    bool remove(key_t to_be_del_k) {
        this -> flush();
        if (this -> keys.find(to_be_del_k) == this -> keys.end()) {
            return this -> fail;
        }

        typename std::list <memdb_column>::iterator it = this -> keys[to_be_del_k].item;
        this -> keys.erase(it -> key);
        this -> time_touch.erase({it});
        this -> db.erase(it);
        this -> __size--;

        return this -> succ;
    }

    bool update(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch=false) {
        this -> flush();
        if (this -> keys.find(to_be_udt_k) == this -> keys.end()) {
            return this -> fail;
        }

        typename std::list <memdb_column>::iterator it = this -> keys[to_be_udt_k].item;
        it -> value = udt_v;
        if (need_to_udt_timetouch == true) {
            std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
            T copy_k = it -> key;
            U copy_v = it -> value;
            this -> remove(copy_k);
            this -> insert(copy_k, copy_v);
        }

        return this -> succ;
    }

    std::optional<U> search(key_t to_be_sch_k) {
        this -> flush();
        if (this -> keys.find(to_be_sch_k) == this -> keys.end()) {
            return std::nullopt;
        }

        typename std::list <memdb_column>::iterator it = this -> keys[to_be_sch_k].item;
        return it -> value;
    }
};