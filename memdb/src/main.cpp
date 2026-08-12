#include <bits/stdc++.h>
#include "main/memdb.cpp"
using namespace std;
Memdb<string,string> testdb(10);
int main(){
    testdb.insert_("key1","value1");
    auto pack=testdb.search_("key1");
    cout<<(pack==nullopt?"未找到":pack.value())<<endl;
    this_thread::sleep_for(std::chrono::seconds(10));
    auto pack2=testdb.search_("key1");
    cout<<(pack2==nullopt?"未找到":pack2.value())<<endl;
    return 0;
}