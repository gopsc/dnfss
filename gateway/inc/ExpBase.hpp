#ifndef EXCEPTION_BASE_H
#define EXCEPTION_BASE_H

#include <exception>
#include <string>
#include <cstring>
#include <memory>
#include <source_location>
#include <sstream>
#include <type_traits>

/**
 * @brief 通用异常基类
 * 
 * 特性：
 * 1. 完整的错误信息管理（错误码、消息、位置信息）
 * 2. 支持异常链（嵌套异常）
 * 3. 多态支持（clone、自定义消息）
 * 4. 线程安全的 what() 缓存
 * 5. 支持 C++20 source_location
 */
class ExceptionBase : public std::exception {
public:
    // ============ 构造函数 ============
    
    explicit ExceptionBase(
        int code = 0,
        const std::string& message = "",
        const std::source_location& location = std::source_location::current()
    ) noexcept
        : m_code(code)
        , m_message(message)
        , m_file(location.file_name())
        , m_line(location.line())
        , m_function(location.function_name())
        , m_what_cache()
    {
    }

    // 支持从字符串字面量构造
    explicit ExceptionBase(
        const std::string& message,
        const std::source_location& location = std::source_location::current()
    ) noexcept
        : m_code(0)
        , m_message(message)
        , m_file(location.file_name())
        , m_line(location.line())
        , m_function(location.function_name())
        , m_what_cache()
    {
    }

    // ============ 拷贝和移动 ============
    
    ExceptionBase(const ExceptionBase& other) noexcept
        : std::exception(other)
        , m_code(other.m_code)
        , m_message(other.m_message)
        , m_file(other.m_file)
        , m_line(other.m_line)
        , m_function(other.m_function)
        , m_nested(other.m_nested ? other.m_nested->clone() : nullptr)
        , m_what_cache()
    {
    }

    ExceptionBase(ExceptionBase&& other) noexcept
        : std::exception(std::move(other))
        , m_code(other.m_code)
        , m_message(std::move(other.m_message))
        , m_file(std::move(other.m_file))
        , m_line(other.m_line)
        , m_function(std::move(other.m_function))
        , m_nested(std::move(other.m_nested))
        , m_what_cache(std::move(other.m_what_cache))
    {
        other.m_code = 0;
        other.m_line = 0;
    }

    virtual ~ExceptionBase() noexcept = default;

    // ============ 赋值运算符 ============
    
    ExceptionBase& operator=(const ExceptionBase& other) noexcept {
        if (this != &other) {
            m_code = other.m_code;
            m_message = other.m_message;
            m_file = other.m_file;
            m_line = other.m_line;
            m_function = other.m_function;
            m_nested = other.m_nested ? other.m_nested->clone() : nullptr;
            m_what_cache.clear();
        }
        return *this;
    }

    ExceptionBase& operator=(ExceptionBase&& other) noexcept {
        if (this != &other) {
            m_code = other.m_code;
            m_message = std::move(other.m_message);
            m_file = std::move(other.m_file);
            m_line = other.m_line;
            m_function = std::move(other.m_function);
            m_nested = std::move(other.m_nested);
            m_what_cache = std::move(other.m_what_cache);
            other.m_code = 0;
            other.m_line = 0;
        }
        return *this;
    }

    // ============ 访问器 ============
    
    int code() const noexcept { return m_code; }
    const std::string& message() const noexcept { return m_message; }
    const std::string& file() const noexcept { return m_file; }
    int line() const noexcept { return m_line; }
    const std::string& function() const noexcept { return m_function; }
    const ExceptionBase* nested() const noexcept { return m_nested.get(); }

    // ============ 异常链操作 ============
    
    void set_nested(std::unique_ptr<ExceptionBase> nested) noexcept {
        m_nested = std::move(nested);
        m_what_cache.clear();
    }

    template<typename ExType>
    void set_nested(const ExType& nested_ex) noexcept {
        static_assert(std::is_base_of<ExceptionBase, ExType>::value,
                      "ExType must inherit from ExceptionBase");
        m_nested = std::make_unique<ExType>(nested_ex);
        m_what_cache.clear();
    }

    // ============ std::exception 接口 ============
    
    const char* what() const noexcept override {
        if (m_what_cache.empty()) {
            build_what_message();
        }
        return m_what_cache.c_str();
    }

    // ============ 多态支持 ============
    
    virtual std::unique_ptr<ExceptionBase> clone() const {
        return std::make_unique<ExceptionBase>(*this);
    }

    // ============ 重新抛出 ============
    
    [[noreturn]] void rethrow() const {
        throw *this;
    }

protected:
    // ============ 可重写的消息构建方法 ============
    
    virtual std::string build_message() const {
        std::string result;
        
        // 错误码
        if (m_code != 0) {
            result += "[Error " + std::to_string(m_code) + "] ";
        }
        
        // 错误消息
        result += m_message;
        
        // 位置信息
        if (!m_file.empty()) {
            result += " (at " + m_file;
            if (m_line > 0) {
                result += ":" + std::to_string(m_line);
            }
            if (!m_function.empty()) {
                result += " in " + m_function;
            }
            result += ")";
        }
        
        // 嵌套异常
        if (m_nested) {
            result += "\n  Caused by: " + std::string(m_nested->what());
        }
        
        return result;
    }

private:
    // ============ 内部方法 ============
    
    void build_what_message() const noexcept {
        try {
            m_what_cache = build_message();
        } catch (...) {
            // 降级处理
            m_what_cache = "Exception: " + m_message;
        }
    }

private:
    // ============ 成员变量 ============
    
    int m_code;
    std::string m_message;
    std::string m_file;
    int m_line;
    std::string m_function;
    std::unique_ptr<ExceptionBase> m_nested;
    mutable std::string m_what_cache;
};

// ============================================================================
// 宏定义 - 方案二完整版
// ============================================================================

/**
 * @brief 抛出指定类型的异常（自动添加 source_location）
 * 
 * 适用场景：子类构造函数与 ExceptionBase 相同
 * 
 * 使用示例：
 *   THROW_EX(NetworkException, 404, "Server not found")
 *   THROW_EX(DatabaseException, 100, "Connection timeout")
 */
#define THROW_EX(ExType, code, msg) \
    throw ExType(code, msg, std::source_location::current())

/**
 * @brief 抛出异常（仅消息，不传错误码）
 * 
 * 适用场景：子类支持只传消息的构造函数
 * 
 * 使用示例：
 *   THROW_EX_MSG(ValidationException, "Invalid input data")
 */
#define THROW_EX_MSG(ExType, msg) \
    throw ExType(msg, std::source_location::current())

/**
 * @brief 抛出异常（支持任意构造函数参数）
 * 
 * 适用场景：子类有额外的构造函数参数
 * 
 * 使用示例：
 *   // 子类定义
 *   class FileException : public ExceptionBase {
 *   public:
 *       FileException(int code, const std::string& msg, 
 *                     const std::string& filename,
 *                     const std::source_location& loc = std::source_location::current())
 *           : ExceptionBase(code, msg, loc), m_filename(filename) {}
 *   };
 *   
 *   // 使用
 *   THROW_EX_CUSTOM(FileException, 404, "File not found", "/etc/config.txt")
 */
#define THROW_EX_CUSTOM(ExType, ...) \
    throw ExType(__VA_ARGS__, std::source_location::current())

/**
 * @brief 抛出异常并携带嵌套异常
 * 
 * 适用场景：需要保留原始异常信息
 * 
 * 使用示例：
 *   try {
 *       connect_to_server();
 *   } catch (const NetworkException& e) {
 *       THROW_EX_NESTED(ServiceException, 500, "Service failed", e)
 *   }
 */
#define THROW_EX_NESTED(ExType, code, msg, nested_ex) \
    do { \
        auto ex = ExType(code, msg, std::source_location::current()); \
        ex.set_nested(nested_ex); \
        throw ex; \
    } while(0)

/**
 * @brief 抛出异常并携带嵌套异常（支持自定义参数）
 * 
 * 使用示例：
 *   THROW_EX_NESTED_CUSTOM(FileException, 500, "Read failed", 
 *                          original_ex, "/data/file.txt")
 */
#define THROW_EX_NESTED_CUSTOM(ExType, code, msg, nested_ex, ...) \
    do { \
        auto ex = ExType(code, msg, __VA_ARGS__, std::source_location::current()); \
        ex.set_nested(nested_ex); \
        throw ex; \
    } while(0)

/**
 * @brief 检查条件，失败则抛出异常
 * 
 * 使用示例：
 *   THROW_IF(ptr == nullptr, NullPointerException, 100, "Pointer is null")
 *   THROW_IF(data.empty(), ValidationException, 200, "Data cannot be empty")
 */
#define THROW_IF(cond, ExType, code, msg) \
    do { \
        if (cond) { \
            THROW_EX(ExType, code, msg); \
        } \
    } while(0)

/**
 * @brief 检查条件，失败则抛出异常（仅消息）
 */
#define THROW_IF_MSG(cond, ExType, msg) \
    do { \
        if (cond) { \
            THROW_EX_MSG(ExType, msg); \
        } \
    } while(0)

/**
 * @brief 检查条件，失败则抛出异常（自定义参数）
 */
#define THROW_IF_CUSTOM(cond, ExType, ...) \
    do { \
        if (cond) { \
            THROW_EX_CUSTOM(ExType, __VA_ARGS__); \
        } \
    } while(0)

/**
 * @brief 检查条件，失败则抛出异常（带嵌套异常）
 */
#define THROW_IF_NESTED(cond, ExType, code, msg, nested_ex) \
    do { \
        if (cond) { \
            THROW_EX_NESTED(ExType, code, msg, nested_ex); \
        } \
    } while(0)

/**
 * @brief 检查条件，失败则抛出异常（带嵌套异常和自定义参数）
 */
#define THROW_IF_NESTED_CUSTOM(cond, ExType, code, msg, nested_ex, ...) \
    do { \
        if (cond) { \
            THROW_EX_NESTED_CUSTOM(ExType, code, msg, nested_ex, __VA_ARGS__); \
        } \
    } while(0)

// ============================================================================
// 辅助宏 - 用于格式化错误消息
// ============================================================================

/**
 * @brief 格式化错误消息（使用流式语法）
 * 
 * 使用示例：
 *   THROW_EX_FMT(NetworkException, 404, "Failed to connect to " << host << ":" << port)
 */
#define THROW_EX_FMT(ExType, code, msg_expr) \
    do { \
        std::ostringstream _oss; \
        _oss << msg_expr; \
        THROW_EX(ExType, code, _oss.str()); \
    } while(0)

/**
 * @brief 格式化错误消息（使用 printf 风格）
 * 
 * 使用示例：
 *   THROW_EX_PRINTF(NetworkException, 404, "Failed to connect to %s:%d", host.c_str(), port)
 */
#define THROW_EX_PRINTF(ExType, code, fmt, ...) \
    do { \
        char _buf[1024]; \
        snprintf(_buf, sizeof(_buf), fmt, __VA_ARGS__); \
        THROW_EX(ExType, code, std::string(_buf)); \
    } while(0)

/**
 * @brief 条件检查 + 格式化消息
 */
#define THROW_IF_FMT(cond, ExType, code, msg_expr) \
    do { \
        if (cond) { \
            THROW_EX_FMT(ExType, code, msg_expr); \
        } \
    } while(0)

/**
 * @brief 条件检查 + printf 风格格式化
 */
#define THROW_IF_PRINTF(cond, ExType, code, fmt, ...) \
    do { \
        if (cond) { \
            THROW_EX_PRINTF(ExType, code, fmt, __VA_ARGS__); \
        } \
    } while(0)

#endif // EXCEPTION_BASE_H
