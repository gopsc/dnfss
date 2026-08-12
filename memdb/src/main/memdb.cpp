#include "memdb.hpp"



template <typename T, typename U>

bool Memdb <T, U>::index_timestamp::operator<(const index_timestamp& a) const {
    return this -> item -> timestamp > a.item -> timestamp;
}


template <typename T, typename U>

void Memdb <T, U>::flush() {
    if (this -> time_touch.empty() == true) {
        return;
    }
    std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
    typename std::list <memdb_column>::iterator it = this -> time_touch.top().item;

    while (now_time > it -> timestamp || it -> be_del == true) {
        if (it -> be_del != true) this -> __size--;
        this -> keys.erase(it -> key);
		this -> time_touch.pop();
        this -> db.erase(it);

        if (this -> time_touch.empty() == true) {
            return;
        }
        it = this -> time_touch.top().item;
        now_time = std::chrono::system_clock::now();
    }
}


template <typename T, typename U>

void Memdb <T, U>::update_queue() {
    std::priority_queue <index_timestamp> new_time_touch;
    while (this -> time_touch.empty() == false) {
        new_time_touch.push(this -> time_touch.top());
        this -> time_touch.pop();
    }
    this -> time_touch = new_time_touch;
}


template <typename T, typename U>

Memdb <T, U>::Memdb() = default;


template <typename T, typename U>

Memdb <T, U>::Memdb(int max_time) : MAX_TIME(max_time) {}


template <typename T, typename U>

Memdb <T, U>& Memdb <T, U>::operator=(const Memdb <T, U>& to_be_cp_memdb) = default;


template <typename T, typename U>

Memdb <T, U>::~Memdb() = default;


template <typename T, typename U>

size_t Memdb <T, U>::size() const {
    return this -> __size;
}


template <typename T, typename U>

const std::unordered_map <T, typename Memdb <T, U>::index_keys>& Memdb <T, U>::get_keys() const {
    return this -> keys;
}


template <typename T, typename U>

const std::list <typename Memdb <T, U>::memdb_column>& Memdb <T, U>::get_db() const {
    return this -> db;
}


template <typename T, typename U>

const std::priority_queue <typename Memdb <T, U>::index_timestamp>& Memdb <T, U>::get_time_touch() const {
    return this -> time_touch;
}



template <typename T, typename U>

bool Memdb <T, U>::insert(key_t to_be_ins_k, value_t ins_v) {
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
    this -> time_touch.push(new_time_touch);

    this -> __size++;

    return this -> succ;
}


template <typename T, typename U>

bool Memdb <T, U>::remove(key_t to_be_del_k) {
    this -> flush();
    if (this -> keys.find(to_be_del_k) == this -> keys.end()) {
        return this -> fail;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_del_k].item;
    it -> be_del = true;
    this -> keys.erase(it -> key);
    this -> __size--;

    return this -> succ;
}


template <typename T, typename U>

bool Memdb <T, U>::update(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch) {
    this -> flush();
    if (this -> keys.find(to_be_udt_k) == this -> keys.end()) {
        return this -> fail;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_udt_k].item;
    it -> value = udt_v;
    if (need_to_udt_timetouch == true) {
        std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
        it -> timestamp = now_time + std::chrono::seconds(MAX_TIME);
        this -> update_queue();
    }

    return this -> succ;
}


template <typename T, typename U>

std::optional<U> Memdb <T, U>::search(key_t to_be_sch_k){
    this -> flush();
    if (this -> keys.find(to_be_sch_k) == this -> keys.end()) {
        return std::nullopt;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_sch_k].item;
    return it -> value;
}


/*
内测版
*/


template <typename T, typename U>

void Memdb <T, U>::flush_() {
    if (this -> time_touch_.empty() == true) {
        return;
    }
    std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
    typename std::list <memdb_column>::iterator it = this -> time_touch_.begin() -> item;

    while (now_time > it -> timestamp || it -> be_del == true) {
        if (it -> be_del != true) this -> __size--;
        key_t to_be_del_k = it -> key;
        this -> db.erase(it);
        this -> keys.erase(to_be_del_k);
		this -> time_touch_.erase(this -> time_touch_.begin());

        if (this -> time_touch_.empty() == true) {
            return;
        }
        it = this -> time_touch_.begin() -> item;
        now_time = std::chrono::system_clock::now();
    }
}


template <typename T, typename U>

const std::multiset <typename Memdb <T, U>::index_timestamp>& Memdb <T, U>::get_time_touch_() const {
    return this -> time_touch_;
}


template <typename T, typename U>

bool Memdb <T, U>::insert_(key_t to_be_ins_k, value_t ins_v) {
    this -> flush_();
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
    this -> time_touch_.insert(new_time_touch);

    this -> __size++;

    return this -> succ;
}


template <typename T, typename U>

bool Memdb <T, U>::remove_(key_t to_be_del_k) {
    this -> flush_();
    if (this -> keys.find(to_be_del_k) == this -> keys.end()) {
        return this -> fail;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_del_k].item;
    it -> be_del = true;
    this -> keys.erase(it -> key);
    this -> __size--;

    return this -> succ;
}


template <typename T, typename U>

bool Memdb <T, U>::update_(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch) {
    this -> flush_();
    if (this -> keys.find(to_be_udt_k) == this -> keys.end()) {
        return this -> fail;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_udt_k].item;
    it -> value = udt_v;
    if (need_to_udt_timetouch == true) {
        std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
        it -> timestamp = now_time + std::chrono::seconds(MAX_TIME);
    }

    return this -> succ;
}


template <typename T, typename U>

std::optional<U> Memdb <T, U>::search_(key_t to_be_sch_k){
    this -> flush_();
    if (this -> keys.find(to_be_sch_k) == this -> keys.end()) {
        return std::nullopt;
    }

    typename std::list <memdb_column>::iterator it = this -> keys[to_be_sch_k].item;
    return it -> value;
}