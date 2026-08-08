#include "memdb.hpp"



template <typename T, typename U>

bool Memdb <T, U>::index_timestamp::operator<(const index_timestamp& a) const {
    return this -> item -> timestamp > a.item -> timestamp;
}


template <typename T, typename U>

void Memdb <T, U>::flush() {
    std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
    std::list <memdb_column>::iterator it = this -> time_touch.front().item;

    while (now_time > it -> timestamp || it -> be_del == true) {
        this -> keys.erase(it -> key);
        this -> db.erase(it);
		this -> time_touch.pop();
		this -> __size--;
        it = this -> time_touch.front().item;
        now_time = std::chrono::system_clock::now();
    }
}

template <typename T, typename U>

Memdb <T, U>::Memdb() = default;


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

void Memdb <T, U>::insert(key_t to_be_ins_k, value_t ins_v) {
    this -> flush();

    std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();

    Memdb::memdb_column new_column;
    new_column.key = to_be_ins_k;
    new_column.value = ins_v;
    new_column.timestamp = now_time;
    this -> db.push_back(new_column);

    Memdb::index_keys new_key;
    new_key.key = to_be_ins_k;
    new_key.item = std::prev(this -> db.end());
    this -> keys.insert(new_key);

    Memdb::index_timestamp new_time_touch;
    new_time_touch.item = std::prev(this -> db.end());
    this -> time_touch.push(new_time_touch);
}


template <typename T, typename U>

void Memdb <T, U>::remove(key_t to_be_del_k) {
    this -> flush();

    std::list <memdb_column>::iterator it = this -> keys[to_be_del_k].item;
    it -> be_del = true;
}


template <typename T, typename U>

void Memdb <T, U>::update(key_t to_be_udt_k, value_t udt_v, bool need_to_udt_timetouch=false) {
    this -> flush();

    std::list <memdb_column>::iterator it = this -> keys[to_be_udt_k];  //! 这边会默认插入
    it -> value = udt_v;
    if (need_to_udt_timetouch == true) {
        std::chrono::system_clock::time_point now_time = std::chrono::system_clock::now();
        it -> timestamp = now_time;
    }
}


template <typename T, typename U>

Memdb <T, U>::value_t Memdb <T, U>::search(key_t to_be_sch_k) const {
    this -> flush();

    std::list <memdb_column>::iterator it = this -> keys[to_be_sch_k];  //! 这边会默认插入
    return it -> value;
}
