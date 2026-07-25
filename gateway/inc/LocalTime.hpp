#pragma once
#include <chrono>
#include <string>
#include <format>
#include <optional>
#include <sstream>
namespace qing {

class LocalTime {
public:
    // 获取当前本地时间字符串
    static std::string getCurrent(const std::string& format = "%Y-%m-%d %H:%M:%S") {
        using namespace std::chrono;
        auto now = system_clock::now();
        auto zone = current_zone();
        zoned_time zt{zone, now};
        return std::vformat("{:" + format + "}", std::make_format_args(zt));
    }

    // 解析时间字符串为 system_clock::time_point（本地时间 → UTC）
    static std::optional<std::chrono::system_clock::time_point> parse(
        const std::string& str,
        const std::string& format = "%Y-%m-%d %H:%M:%S"
    ) {
        using namespace std::chrono;
        std::istringstream iss{str};
        local_time<seconds> lt;
        iss >> std::chrono::parse(format, lt);   // ✅ 修复
        if (iss.fail()) {
            return std::nullopt;
        }
        auto zone = current_zone();
        zoned_time zt{zone, lt};
        return zt.get_sys_time();
    }

    // ----- 过期判断 -----
    static bool isExpired(const std::chrono::system_clock::time_point& tp) {
        using namespace std::chrono;
        return system_clock::now() > tp;
    }

    template <typename Rep, typename Period>
    static bool isExpired(const std::chrono::system_clock::time_point& tp,
                          const std::chrono::duration<Rep, Period>& gracePeriod) {
        using namespace std::chrono;
        return system_clock::now() > tp + gracePeriod;
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
