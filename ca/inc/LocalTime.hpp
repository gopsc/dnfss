#pragma once
#include <chrono>
#include <string>
#include <optional>
#include <sstream>
#include <iomanip>
#include <ctime>
namespace qing {

class LocalTime {
public:
    // 获取当前本地时间字符串
    static std::string getCurrent(const std::string& format = "%Y-%m-%d %H:%M:%S") {
        auto now = std::chrono::system_clock::now();
        std::time_t t = std::chrono::system_clock::to_time_t(now);
        std::tm tm_buf{};
        localtime_r(&t, &tm_buf);
        char buf[128];
        std::strftime(buf, sizeof(buf), format.c_str(), &tm_buf);
        return std::string(buf);
    }

    // 解析时间字符串为 system_clock::time_point（本地时间 → UTC）
    static std::optional<std::chrono::system_clock::time_point> parse(
        const std::string& str,
        const std::string& format = "%Y-%m-%d %H:%M:%S"
    ) {
        std::tm tm_buf{};
        std::istringstream iss{str};
        iss >> std::get_time(&tm_buf, format.c_str());
        if (iss.fail()) {
            return std::nullopt;
        }
        // mktime 将本地时间转换为 time_t(UTC)
        std::time_t t = std::mktime(&tm_buf);
        if (t == -1) {
            return std::nullopt;
        }
        return std::chrono::system_clock::from_time_t(t);
    }

    // ----- 过期判断 -----
    static bool isExpired(const std::chrono::system_clock::time_point& tp) {
        return std::chrono::system_clock::now() > tp;
    }

    template <typename Rep, typename Period>
    static bool isExpired(const std::chrono::system_clock::time_point& tp,
                          const std::chrono::duration<Rep, Period>& gracePeriod) {
        return std::chrono::system_clock::now() > tp + gracePeriod;
    }

    static bool isExpired(const std::string& timeStr,
                          const std::string& format = "%Y-%m-%d %H:%M:%S") {
        auto parsed = parse(timeStr, format);
        if (!parsed) return true;
        return isExpired(*parsed);
    }

    template <typename Rep, typename Period>
    static bool isExpired(const std::string& timeStr,
                          const std::chrono::duration<Rep, Period>& gracePeriod,
                          const std::string& format = "%Y-%m-%d %H:%M:%S") {
        auto parsed = parse(timeStr, format);
        if (!parsed) return true;
        return isExpired(*parsed, gracePeriod);
    }
};


}
