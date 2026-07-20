#pragma once
#include <string>
#include <functional>
#include <map>
#include <curl/curl.h>

namespace pmc::net {

enum class HttpMethod {
    GET,
    POST,
    PUT,
    DELETE,
    PATCH,
    HEAD,
    OPTIONS
};

class HttpClient {
public:
    HttpClient() {
        curl_ = curl_easy_init();
        if (!curl_) {
            throw std::runtime_error("Failed to initialize CURL");
        }
    }
    
    ~HttpClient() {
        if (curl_) {
            curl_easy_cleanup(curl_);
        }
        if (headers_) {
            curl_slist_free_all(headers_);
        }
    }
    
    void set_header(const std::string& header) {
        headers_ = curl_slist_append(headers_, header.c_str());
    }
    
    void clear_headers() {
        if (headers_) {
            curl_slist_free_all(headers_);
            headers_ = nullptr;
        }
    }
    
    const std::map<std::string, std::string>& get_response_headers() const {
        return response_headers_;
    }
    
    long get_response_code() const {
        return response_code_;
    }
    
    // ===== 修复：直接转发，不按行处理 =====
    void request_stream_sync(
        HttpMethod method,
        const std::string& url,
        const std::string& data,
        std::function<void(const std::string&)> callback
    ) {
        if (!curl_) {
            throw std::runtime_error("CURL not initialized");
        }
        
        response_headers_.clear();
        response_code_ = 0;
        
        curl_easy_reset(curl_);
        curl_easy_setopt(curl_, CURLOPT_URL, url.c_str());
        set_http_method(method, data);
        curl_easy_setopt(curl_, CURLOPT_HTTPHEADER, headers_);
        curl_easy_setopt(curl_, CURLOPT_HEADERFUNCTION, header_callback);
        curl_easy_setopt(curl_, CURLOPT_HEADERDATA, this);
        
        // ===== 关键修复：直接收集原始数据，不按行处理 =====
        std::string full_response;
        curl_easy_setopt(curl_, CURLOPT_WRITEFUNCTION, write_callback);
        curl_easy_setopt(curl_, CURLOPT_WRITEDATA, &full_response);
        
        curl_easy_setopt(curl_, CURLOPT_TIMEOUT, 30L);
        curl_easy_setopt(curl_, CURLOPT_CONNECTTIMEOUT, 10L);
        
        CURLcode res = curl_easy_perform(curl_);
        
        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }
        
        curl_easy_getinfo(curl_, CURLINFO_RESPONSE_CODE, &response_code_);
        
        // ===== 完整转发所有数据（包括换行符） =====
        if (!full_response.empty()) {
            callback(full_response);
        }
    }
    
    void get_stream_sync(const std::string& url, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::GET, url, "", callback);
    }
    
    void post_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::POST, url, data, callback);
    }
    
    void put_stream_sync(const std::string& url, const std::string& data, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::PUT, url, data, callback);
    }
    
    void delete_stream_sync(const std::string& url, std::function<void(const std::string&)> callback) {
        request_stream_sync(HttpMethod::DELETE, url, "", callback);
    }

private:
    CURL* curl_ = nullptr;
    struct curl_slist* headers_ = nullptr;
    std::map<std::string, std::string> response_headers_;
    long response_code_ = 0;
    
    void set_http_method(HttpMethod method, const std::string& data) {
        switch (method) {
            case HttpMethod::GET:
                curl_easy_setopt(curl_, CURLOPT_HTTPGET, 1L);
                break;
                
            case HttpMethod::POST:
                curl_easy_setopt(curl_, CURLOPT_POST, 1L);
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
                
            case HttpMethod::PUT:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "PUT");
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
                
            case HttpMethod::DELETE:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "DELETE");
                break;
                
            case HttpMethod::PATCH:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "PATCH");
                if (!data.empty()) {
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDS, data.c_str());
                    curl_easy_setopt(curl_, CURLOPT_POSTFIELDSIZE, data.size());
                }
                break;
                
            case HttpMethod::HEAD:
                curl_easy_setopt(curl_, CURLOPT_NOBODY, 1L);
                break;
                
            case HttpMethod::OPTIONS:
                curl_easy_setopt(curl_, CURLOPT_CUSTOMREQUEST, "OPTIONS");
                break;
        }
    }
    
    static size_t header_callback(char* buffer, size_t size, size_t nitems, void* userdata) {
        size_t total_size = size * nitems;
        HttpClient* client = static_cast<HttpClient*>(userdata);
        
        std::string header_line(buffer, total_size);
        size_t colon_pos = header_line.find(':');
        if (colon_pos != std::string::npos) {
            std::string key = header_line.substr(0, colon_pos);
            std::string value = header_line.substr(colon_pos + 1);
            
            key.erase(0, key.find_first_not_of(" \t"));
            key.erase(key.find_last_not_of(" \t") + 1);
            value.erase(0, value.find_first_not_of(" \t"));
            value.erase(value.find_last_not_of(" \t\r\n") + 1);
            
            if (!key.empty() && !value.empty()) {
                client->response_headers_[key] = value;
            }
        }
        
        return total_size;
    }
    
    // ===== 修复：直接写入原始数据，不按行处理 =====
    static size_t write_callback(char* ptr, size_t size, size_t nmemb, void* userdata) {
        size_t total_size = size * nmemb;
        std::string* response = static_cast<std::string*>(userdata);
        
        // 直接追加原始数据，保留所有换行符和特殊字符
        response->append(ptr, total_size);
        
        return total_size;
    }
};

} // namespace pmc::net
