/*
 * captcha2 — C++20 验证码服务（pymsg RPC）
 *
 * 基于 libmsg RpcServer + FreeType 字体渲染 + stb_image_write PNG 编码。
 * 协议与 Python captcha 完全兼容，gateway 零改动。
 */

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

#include <us/Msg.hpp>
#include <us/RpcServer.hpp>
#include <us/RpcClient.hpp>
#include <us/AsyncQueue.hpp>

#include <boost/json.hpp>
#include <boost/program_options.hpp>

#include <ft2build.h>
#include FT_FREETYPE_H

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "LogC.hpp"

namespace json = boost::json;
namespace po = boost::program_options;

using namespace captcha2;

// ═══════════════════════════════════════════════════════════════════════════
// 1. 配置结构
// ═══════════════════════════════════════════════════════════════════════════

// P3-1: 安全的 stoi 封装，异常时输出键名与原始值便于排障
static int parse_int(const std::string& key, const std::string& val) {
	try {
		return std::stoi(val);
	} catch (const std::exception& ex) {
		std::cerr << "[captcha2] 配置解析失败: " << key << " = '" << val
		          << "' (" << ex.what() << ")" << std::endl;
		std::exit(1);
	}
}

struct CaptchaConfig {
	std::string host = "127.0.0.1";
	int port = 8009;
	int workers = 4;

	int len = 4;
	int ttl = 300;
	std::string font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf";
	int max_store = 10000;
	int rate_limit_per_min = 600;  // P1-1: 全局限流仅作最后防线，主要限流由 gateway 按 IP 负责
};

// 从 confs 拉取指定名称的配置文件全文，阻塞等待 confs 就绪
static std::string fetch_config_from_confs(const std::string& confs_host, int confs_port,
		const std::string& config_name) {
	pmc::net::RpcClient client(confs_host, confs_port, "captcha2",
		/*timeout=*/5.0, /*retry_on_refused=*/true);
	try {
		auto data = client.call("get", {{"name", config_name}});
		auto* content = data.as_object().if_contains("content");
		if (!content || !content->is_string()) {
			std::cerr << "[captcha2] confs 返回的 captcha.ini 无 content 字段" << std::endl;
			std::exit(1);
		}
		return json::value_to<std::string>(*content);
	} catch (const std::exception& e) {
		std::cerr << "[captcha2] 致命错误: 从 confs 获取 captcha.ini 失败: " << e.what() << std::endl;
		std::exit(1);
	}
}

// 把 ini 文本合并进 config（仅覆盖已知 section/key，白名单策略）
static void merge_ini(CaptchaConfig& cfg, const std::string& text) {
	std::istringstream stream(text);
	std::string line, section;
	while (std::getline(stream, line)) {
		// trim
		auto start = line.find_first_not_of(" \t\r");
		if (start == std::string::npos || line[start] == '#' || line[start] == ';')
			continue;
		auto end = line.find_last_not_of(" \t\r");
		line = line.substr(start, end - start + 1);

		if (line.front() == '[' && line.back() == ']') {
			section = line.substr(1, line.size() - 2);
			continue;
		}
		auto eq = line.find('=');
		if (eq == std::string::npos) continue;
		std::string key = line.substr(0, eq);
		std::string val = line.substr(eq + 1);
		// trim key
		auto ke = key.find_last_not_of(" \t");
		if (ke != std::string::npos) key = key.substr(0, ke + 1);
		auto ks = key.find_first_not_of(" \t");
		if (ks != std::string::npos) key = key.substr(ks);
		// trim val
		auto vs = val.find_first_not_of(" \t");
		if (vs != std::string::npos) val = val.substr(vs);
		auto ve = val.find_last_not_of(" \t");
		if (ve != std::string::npos) val = val.substr(0, ve + 1);

		if (section == "server") {
			if (key == "host") cfg.host = val;
			else if (key == "port") cfg.port = parse_int(key, val);
			else if (key == "workers") cfg.workers = parse_int(key, val);
		} else if (section == "captcha") {
			if (key == "len") cfg.len = parse_int(key, val);
			else if (key == "ttl") cfg.ttl = parse_int(key, val);
			else if (key == "font_path") cfg.font_path = val;
			else if (key == "max_store") cfg.max_store = parse_int(key, val);
			else if (key == "rate_limit_per_min") cfg.rate_limit_per_min = parse_int(key, val);
		}
	}
}

// 环境变量覆盖（最高优先级，Docker 兼容）
static void apply_env_overrides(CaptchaConfig& cfg) {
	if (auto* v = std::getenv("LISTEN_ADDR"))       cfg.host = v;
	if (auto* v = std::getenv("LISTEN_PORT"))       cfg.port = parse_int("LISTEN_PORT", v);
	if (auto* v = std::getenv("WORKERS"))           cfg.workers = parse_int("WORKERS", v);
	if (auto* v = std::getenv("CAPTCHA_FONT_PATH")) cfg.font_path = v;
	if (auto* v = std::getenv("CAPTCHA_MAX_STORE")) cfg.max_store = parse_int("CAPTCHA_MAX_STORE", v);
	// P3-3: 补齐 captcha 段环境变量覆盖
	if (auto* v = std::getenv("CAPTCHA_LEN"))        cfg.len = parse_int("CAPTCHA_LEN", v);
	if (auto* v = std::getenv("CAPTCHA_TTL"))        cfg.ttl = parse_int("CAPTCHA_TTL", v);
	if (auto* v = std::getenv("CAPTCHA_RATE_LIMIT")) cfg.rate_limit_per_min = parse_int("CAPTCHA_RATE_LIMIT", v);
}

// 配置完整性校验
static void validate_config(const CaptchaConfig& cfg) {
	if (cfg.host.empty()) { std::cerr << "[captcha2] 配置缺失: server.host" << std::endl; std::exit(1); }
	if (cfg.port <= 0 || cfg.port > 65535) { std::cerr << "[captcha2] 配置无效: server.port" << std::endl; std::exit(1); }
	if (cfg.workers < 0) { std::cerr << "[captcha2] workers 不能为负数" << std::endl; std::exit(1); }
	if (cfg.len < 2 || cfg.len > 8) { std::cerr << "[captcha2] captcha.len 应在 2~8 之间" << std::endl; std::exit(1); }
	if (cfg.ttl < 10) { std::cerr << "[captcha2] captcha.ttl 至少 10 秒" << std::endl; std::exit(1); }
	if (cfg.max_store <= 0) { std::cerr << "[captcha2] captcha.max_store 必须为正整数" << std::endl; std::exit(1); }
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. Base64 编码
// ═══════════════════════════════════════════════════════════════════════════

static const char kBase64Chars[] =
	"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static std::string base64_encode(const std::vector<uint8_t>& data) {
	std::string out;
	out.reserve(((data.size() + 2) / 3) * 4);
	for (size_t i = 0; i < data.size(); i += 3) {
		uint32_t n = (uint32_t(data[i]) << 16);
		if (i + 1 < data.size()) n |= (uint32_t(data[i + 1]) << 8);
		if (i + 2 < data.size()) n |= uint32_t(data[i + 2]);

		out.push_back(kBase64Chars[(n >> 18) & 0x3F]);
		out.push_back(kBase64Chars[(n >> 12) & 0x3F]);
		out.push_back((i + 1 < data.size()) ? kBase64Chars[(n >> 6) & 0x3F] : '=');
		out.push_back((i + 2 < data.size()) ? kBase64Chars[n & 0x3F] : '=');
	}
	return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. CaptchaStore — 线程安全 Token 存储
// ═══════════════════════════════════════════════════════════════════════════

class CaptchaStore {
public:
	struct Entry {
		std::string answer;
		std::chrono::steady_clock::time_point created;
	};

	std::string generate(const std::string& answer, int ttl_seconds, int max_size) {
		std::lock_guard<std::mutex> lock(mtx_);
		expire_locked(ttl_seconds);

		if ((int)store_.size() >= max_size)
			throw std::runtime_error("too many pending captchas");

		// 生成 256-bit 随机 Token（64 字符 hex）
		std::array<uint8_t, 32> rand_bytes;
		std::random_device rd;
		for (auto& b : rand_bytes) b = static_cast<uint8_t>(rd());
		std::ostringstream oss;
		oss << std::hex;
		for (auto b : rand_bytes) oss << std::setw(2) << std::setfill('0') << (int)b;

		std::string token = oss.str();
		store_[token] = {answer, std::chrono::steady_clock::now()};
		return token;
	}

	// 校验 + 一次性消费；返回 nullopt 表示 token 无效；否则返回 {answer_correct: bool}
	struct VerifyResult { bool valid; bool token_found; };
	VerifyResult verify(const std::string& token, const std::string& answer, int ttl_seconds) {
		std::lock_guard<std::mutex> lock(mtx_);
		auto it = store_.find(token);
		if (it == store_.end()) return {false, false};

		auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
			std::chrono::steady_clock::now() - it->second.created).count();
		if (elapsed > ttl_seconds) {
			store_.erase(it);
			return {false, false};
		}

		auto stored_answer = it->second.answer;
		store_.erase(it); // 一次性消费

		// 恒定时间比较
		bool match = (answer.size() == stored_answer.size());
		if (match) {
			int diff = 0;
			for (size_t i = 0; i < answer.size(); ++i)
				diff |= answer[i] ^ stored_answer[i];
			match = (diff == 0);
		}
		return {match, true};
	}

	size_t size() const {
		std::lock_guard<std::mutex> lock(mtx_);
		return store_.size();
	}

private:
	void expire_locked(int ttl_seconds) {
		auto now = std::chrono::steady_clock::now();
		for (auto it = store_.begin(); it != store_.end(); ) {
			auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
				now - it->second.created).count();
			if (elapsed > ttl_seconds)
				it = store_.erase(it);
			else
				++it;
		}
	}

	mutable std::mutex mtx_;
	std::unordered_map<std::string, Entry> store_;
};

// ═══════════════════════════════════════════════════════════════════════════
// 4. generate_text() — 安全随机验证码文本
// ═══════════════════════════════════════════════════════════════════════════

static std::string generate_text(int len) {
	static const char kChars[] =
		"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
	static constexpr size_t kNumChars = sizeof(kChars) - 1;

	// 使用 std::random_device 逐字符生成（非确定性 CSPRNG），
	// 与 Token 生成策略对齐，消除 mt19937 可预测性风险（P2-1）
	std::random_device rd;
	std::string text;
	text.reserve(len);
	for (int i = 0; i < len; ++i)
		text.push_back(kChars[rd() % kNumChars]);
	return text;
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. 图片生成 — FreeType + 像素操作 + stb PNG 编码
// ═══════════════════════════════════════════════════════════════════════════

struct RGB {
	uint8_t r, g, b;
};

static constexpr int kImgWidth = 160;
static constexpr int kImgHeight = 50;
static constexpr int kImgChannels = 3;

// 线程本地随机数生成器（用于图片生成中的各种随机元素）
static thread_local std::mt19937 t_rng(std::random_device{}());

static RGB random_color() {
	std::uniform_int_distribution<int> d(0, 255);
	return {static_cast<uint8_t>(d(t_rng)), static_cast<uint8_t>(d(t_rng)), static_cast<uint8_t>(d(t_rng))};
}

static inline uint8_t* pixel_at(std::vector<uint8_t>& pix, int x, int y) {
	return &pix[(y * kImgWidth + x) * kImgChannels];
}

// 最近邻插值旋转灰度位图（expand 模式），返回旋转后的位图
static std::vector<uint8_t> rotate_bitmap(
	const FT_Bitmap& src, double angle_deg,
	int& out_w, int& out_h)
{
	double rad = angle_deg * M_PI / 180.0;
	double cos_a = std::cos(rad), sin_a = std::sin(rad);

	int sw = static_cast<int>(src.width);
	int sh = static_cast<int>(src.rows);

	// expand: 计算旋转后包围盒
	double corners[4][2] = {
		{0, 0}, {double(sw), 0}, {double(sw), double(sh)}, {0, double(sh)}
	};
	double min_x = 1e9, max_x = -1e9, min_y = 1e9, max_y = -1e9;
	for (auto& c : corners) {
		double rx = c[0] * cos_a - c[1] * sin_a;
		double ry = c[0] * sin_a + c[1] * cos_a;
		if (rx < min_x) min_x = rx;
		if (rx > max_x) max_x = rx;
		if (ry < min_y) min_y = ry;
		if (ry > max_y) max_y = ry;
	}

	out_w = static_cast<int>(std::ceil(max_x - min_x));
	out_h = static_cast<int>(std::ceil(max_y - min_y));

	std::vector<uint8_t> out(out_w * out_h, 0);

	for (int dy = 0; dy < out_h; ++dy) {
		for (int dx = 0; dx < out_w; ++dx) {
			// 逆变换：输出坐标 → 源坐标
			double sx = (dx + min_x) * cos_a + (dy + min_y) * sin_a;
			double sy = -(dx + min_x) * sin_a + (dy + min_y) * cos_a;

			int six = static_cast<int>(std::floor(sx));
			int siy = static_cast<int>(std::floor(sy));
			if (six >= 0 && six < sw && siy >= 0 && siy < sh) {
				// 最近邻
				out[dy * out_w + dx] = src.buffer[siy * src.pitch + six];
			}
		}
	}
	return out;
}

// Alpha 合成旋转后的字符位图到主画布（灰度值 = alpha，0=透明, 255=不透明）
static void composite_char(
	std::vector<uint8_t>& pixels,
	const std::vector<uint8_t>& glyph, int gw, int gh,
	int dst_x, int dst_y, const RGB& color)
{
	for (int gy = 0; gy < gh; ++gy) {
		for (int gx = 0; gx < gw; ++gx) {
			uint8_t alpha = glyph[gy * gw + gx];
			if (alpha == 0) continue;

			int px = dst_x + gx;
			int py = dst_y + gy;
			if (px < 0 || px >= kImgWidth || py < 0 || py >= kImgHeight) continue;

			auto* p = pixel_at(pixels, px, py);
			float a = alpha / 255.0f;
			p[0] = static_cast<uint8_t>(p[0] * (1 - a) + color.r * a);
			p[1] = static_cast<uint8_t>(p[1] * (1 - a) + color.g * a);
			p[2] = static_cast<uint8_t>(p[2] * (1 - a) + color.b * a);
		}
	}
}

// Bresenham 画线
static void draw_line(std::vector<uint8_t>& pixels, int x0, int y0, int x1, int y1, const RGB& color) {
	int dx = std::abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
	int dy = -std::abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
	int err = dx + dy;

	while (true) {
		if (x0 >= 0 && x0 < kImgWidth && y0 >= 0 && y0 < kImgHeight) {
			auto* p = pixel_at(pixels, x0, y0);
			p[0] = color.r; p[1] = color.g; p[2] = color.b;
		}
		if (x0 == x1 && y0 == y1) break;
		int e2 = 2 * err;
		if (e2 >= dy) { err += dy; x0 += sx; }
		if (e2 <= dx) { err += dx; y0 += sy; }
	}
}

// 设置单个像素
static void set_pixel(std::vector<uint8_t>& pixels, int x, int y, const RGB& color) {
	if (x >= 0 && x < kImgWidth && y >= 0 && y < kImgHeight) {
		auto* p = pixel_at(pixels, x, y);
		p[0] = color.r; p[1] = color.g; p[2] = color.b;
	}
}

// 主生成函数：返回 PNG 字节流
static std::vector<uint8_t> generate_image(const std::string& text, const std::string& font_path) {
	// 1. 创建 RGB 画布 + 随机背景色
	std::vector<uint8_t> pixels(kImgWidth * kImgHeight * kImgChannels);
	auto bg = random_color();
	for (int i = 0; i < kImgWidth * kImgHeight; ++i) {
		pixels[i * 3 + 0] = bg.r;
		pixels[i * 3 + 1] = bg.g;
		pixels[i * 3 + 2] = bg.b;
	}

	// 2. 初始化 FreeType
	FT_Library ft;
	if (FT_Init_FreeType(&ft) != 0)
		throw std::runtime_error("FreeType 初始化失败");

	FT_Face face;
	// 尝试指定字体路径，失败回退 DejaVu，再失败退出
	if (FT_New_Face(ft, font_path.c_str(), 0, &face) != 0) {
		std::cerr << "[captcha2] 字体 " << font_path << " 加载失败，尝试默认 DejaVu" << std::endl;
		if (FT_New_Face(ft, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0, &face) != 0) {
			FT_Done_FreeType(ft);
			throw std::runtime_error("无法加载任何 TrueType 字体");
		}
	}

	// 3. 逐字符渲染 + 旋转 + 合成
	std::uniform_int_distribution<int> angle_dist(-30, 30);
	std::uniform_int_distribution<int> size_dist(20, 38);
	std::uniform_int_distribution<int> y_dist(2, 12);
	std::uniform_int_distribution<int> x_jitter(-3, 3);

	for (size_t i = 0; i < text.size(); ++i) {
		int char_size = size_dist(t_rng);
		FT_Set_Pixel_Sizes(face, 0, char_size);

		if (FT_Load_Char(face, text[i], FT_LOAD_RENDER) != 0)
			continue;

		auto& bmp = face->glyph->bitmap;
		if (bmp.buffer == nullptr || bmp.width == 0 || bmp.rows == 0)
			continue;

		auto fg = random_color();
		int angle = angle_dist(t_rng);
		int y = y_dist(t_rng);
		int x = 15 + static_cast<int>(i) * 32 + x_jitter(t_rng);

		int rw, rh;
		auto rotated = rotate_bitmap(bmp, angle, rw, rh);

		// 合成位置：水平居中于 x，y 为顶部留白（与 Python 版语义一致）
		// 底部越界时上移钳制，保证字符完整落在画布内
		int cx = x - rw / 2;
		int cy = y;
		if (cy + rh > kImgHeight) cy = kImgHeight - rh;
		if (cy < 0) cy = 0;
		composite_char(pixels, rotated, rw, rh, cx, cy, fg);
	}

	FT_Done_Face(face);
	FT_Done_FreeType(ft);

	// 4. 4 条随机干扰线
	std::uniform_int_distribution<int> xd(0, kImgWidth - 1);
	std::uniform_int_distribution<int> yd(0, kImgHeight - 1);
	for (int i = 0; i < 4; ++i) {
		draw_line(pixels, xd(t_rng), yd(t_rng), xd(t_rng), yd(t_rng), random_color());
	}

	// 5. 50 个随机噪点
	for (int i = 0; i < 50; ++i) {
		set_pixel(pixels, xd(t_rng), yd(t_rng), random_color());
	}

	// 6. PNG 编码
	std::vector<uint8_t> png_buf;
	stbi_write_png_to_func(
		[](void* ctx, void* data, int size) {
			auto* buf = static_cast<std::vector<uint8_t>*>(ctx);
			buf->insert(buf->end(), static_cast<uint8_t*>(data), static_cast<uint8_t*>(data) + size);
		},
		&png_buf, kImgWidth, kImgHeight, kImgChannels, pixels.data(), kImgWidth * kImgChannels);

	return png_buf;
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. 滑动窗口速率限制
// ═══════════════════════════════════════════════════════════════════════════

class RateLimiter {
public:
	bool allow(int max_per_minute) {
		if (max_per_minute <= 0) return true; // 0 = 不限制

		std::lock_guard<std::mutex> lock(mtx_);
		auto now = std::chrono::steady_clock::now();

		while (!timestamps_.empty() &&
			std::chrono::duration_cast<std::chrono::seconds>(
				now - timestamps_.front()).count() > 60) {
			timestamps_.pop_front();
		}

		if (static_cast<int>(timestamps_.size()) >= max_per_minute)
			return false;

		timestamps_.push_back(now);
		return true;
	}

private:
	std::deque<std::chrono::steady_clock::time_point> timestamps_;
	std::mutex mtx_;
};

// ═══════════════════════════════════════════════════════════════════════════
// 7. main() — RpcServer 启动
// ═══════════════════════════════════════════════════════════════════════════

static CaptchaConfig g_config;
static CaptchaStore g_store;
static RateLimiter g_rate_limiter;
static auto& g_log = LogC::instance();

int main(int argc, char* argv[]) {
	try {
		// ── 命令行参数 ──
		std::string confs_host = "127.0.0.1";
		int confs_port = 8010;
		std::string confs_config_name = "captcha.ini";  // --config 覆盖
		std::string instance = "captcha2";               // --instance 覆盖
		std::string log_level = "info";                  // --log-level 覆盖
		{
			const char* env;
			if ((env = std::getenv("CONFS_HOST"))) confs_host = env;
			if ((env = std::getenv("CONFS_PORT"))) confs_port = std::stoi(env);
		}

		po::options_description desc("captcha2 — pymsg 验证码服务");
		desc.add_options()
			("help,h", "显示帮助")
			("addr,a", po::value<std::string>(), "监听地址（覆盖 confs，别名 --host）")
			("host", po::value<std::string>(), "监听地址（覆盖 confs，别名 --addr/-a）")
			("port,p", po::value<int>(), "监听端口（覆盖 confs）")
			("workers,w", po::value<int>(), "工作线程数（覆盖 confs）")
			("config", po::value<std::string>(), "confs 拉取的配置名（默认 captcha.ini）")
			("log-level", po::value<std::string>(), "日志级别 debug|info|warn|error，默认 info")
			("instance", po::value<std::string>(), "实例名（注册中心用，默认 captcha2）");
		po::variables_map vm;
		po::store(po::parse_command_line(argc, argv, desc), vm);
		po::notify(vm);

		if (vm.count("help")) {
			std::cout << desc << std::endl;
			return 0;
		}

		// 预扫描 --config / --instance / --log-level / --host（confs 拉取前处理）
		if (vm.count("config"))   confs_config_name = vm["config"].as<std::string>();
		if (vm.count("instance")) instance = vm["instance"].as<std::string>();
		if (vm.count("log-level")) log_level = vm["log-level"].as<std::string>();

		// ── 配置加载：confs → env → 命令行 ──
		std::string ini_text = fetch_config_from_confs(confs_host, confs_port, confs_config_name);
		merge_ini(g_config, ini_text);

		apply_env_overrides(g_config);

		if (vm.count("addr"))    g_config.host = vm["addr"].as<std::string>();
		if (vm.count("host"))    g_config.host = vm["host"].as<std::string>();
		if (vm.count("port"))    g_config.port = vm["port"].as<int>();
		if (vm.count("workers")) g_config.workers = vm["workers"].as<int>();

		validate_config(g_config);

		g_log.set_level(log_level);

		std::cerr << "[captcha2] 配置来源: confs " << confs_config_name << " @ "
			<< confs_host << ":" << confs_port << std::endl;
		std::cerr << "[captcha2] instance=" << instance
			<< " log-level=" << log_level << std::endl;
		std::cerr << "[captcha2] captcha_len=" << g_config.len
			<< " ttl=" << g_config.ttl
			<< " max_store=" << g_config.max_store
			<< " rate_limit=" << g_config.rate_limit_per_min << "/min" << std::endl;

		// 校验字体文件存在
		{
			FILE* ftest = std::fopen(g_config.font_path.c_str(), "rb");
			if (!ftest) {
				std::cerr << "[captcha2] 致命错误: 字体文件不存在 " << g_config.font_path << std::endl;
				return 1;
			}
			std::fclose(ftest);
		}

		// ── 启动 RpcServer ──
		g_log.info("captcha2", "启动中... 监听 tcp://" + g_config.host + ":" + std::to_string(g_config.port));

		pmc::net::RpcServer rpc(g_config.host, g_config.port,
			pmc::net::MsgServerConfig{.workers = g_config.workers});

		// 路由: image
		rpc.add_route("image", [](const json::object&) -> json::value {
			// 速率限制（P3-5: 抛异常由 RpcServer 包装为 success:false，gateway 映射 429）
			if (!g_rate_limiter.allow(g_config.rate_limit_per_min)) {
				g_log.warning("captcha2", "速率限制触发，拒绝生成验证码");
				throw std::runtime_error("rate_limit_exceeded");
			}

			std::string text = generate_text(g_config.len);

			// 小写化存储，与 Python 版行为对齐（大小写不敏感校验）
			std::string answer_lc = text;
			std::transform(answer_lc.begin(), answer_lc.end(), answer_lc.begin(),
				[](unsigned char c) { return static_cast<char>(std::tolower(c)); });

			// P3-5: generate 异常直接传播（RpcServer 自动包装为 success:false，gateway 映射 503）
			std::string token = g_store.generate(answer_lc, g_config.ttl, g_config.max_store);

			auto png_bytes = generate_image(text, g_config.font_path);
			auto png_b64 = base64_encode(png_bytes);

			g_log.info("captcha2", "验证码已生成 token=" + token.substr(0, 8) + "...");

			json::object data;
			data["token"] = token;
			data["image"] = png_b64;
			return data;
		});

		// 路由: verify
		rpc.add_route("verify", [](const json::object& req) -> json::value {
			std::string token, answer;

			auto it_tok = req.find("token");
			auto it_ans = req.find("answer");
			if (it_tok == req.end() || it_ans == req.end() ||
				!it_tok->value().is_string() || !it_ans->value().is_string()) {
				json::object data;
				data["valid"] = false;
				data["error"] = "token and answer required";
				return data;
			}

			token = json::value_to<std::string>(it_tok->value());
			answer = json::value_to<std::string>(it_ans->value());

			// 小写化（使用 unsigned char 避免 UB）
			std::transform(answer.begin(), answer.end(), answer.begin(),
				[](unsigned char c) { return static_cast<char>(std::tolower(c)); });

			auto result = g_store.verify(token, answer, g_config.ttl);

			if (!result.token_found) {
				g_log.warning("captcha2", "验证码校验失败-无效Token token=" + token.substr(0, std::min<size_t>(8, token.size())) + "...");
				json::object data;
				data["valid"] = false;
				data["error"] = "invalid or expired token";
				return data;
			}

			if (!result.valid) {
				g_log.warning("captcha2", "验证码校验失败-答案错误 token=" + token.substr(0, std::min<size_t>(8, token.size())) + "...");
			}

			json::object data;
			data["valid"] = result.valid;
			return data;
		});

		// 路由: health
		rpc.add_route("health", [](const json::object&) -> json::value {
			json::object data;
			data["status"] = "ok";
			data["service"] = "captcha";
			return data;
		});

		std::cerr << "[captcha2] 监听 tcp://" << g_config.host << ":" << g_config.port << std::endl;
		g_log.info("captcha2", "开始监听 tcp://" + g_config.host + ":" + std::to_string(g_config.port));

		rpc.start(); // 阻塞

	} catch (const std::exception& e) {
		std::cerr << "[captcha2] 致命错误: " << e.what() << std::endl;
		g_log.error("captcha2", std::string("致命错误: ") + e.what());
		return 1;
	}

	return 0;
}
