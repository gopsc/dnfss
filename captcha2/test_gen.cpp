/*
 * test_gen.cpp — 快速验证图片生成（不依赖 confs/RpcServer）
 * 用法: make -f Makefile.test && ./test_gen.bin
 */
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "inc/stb_image_write.h"

#include <ft2build.h>
#include FT_FREETYPE_H

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <random>
#include <sstream>
#include <vector>

// ── 复制 main.cpp 中的核心代码（简化为测试版）───────────────────────

static constexpr int kImgWidth = 160;
static constexpr int kImgHeight = 50;
static constexpr int kImgChannels = 3;

struct RGB { uint8_t r, g, b; };
static thread_local std::mt19937 t_rng(std::random_device{}());

static RGB random_color() {
	std::uniform_int_distribution<int> d(0, 255);
	return {static_cast<uint8_t>(d(t_rng)), static_cast<uint8_t>(d(t_rng)), static_cast<uint8_t>(d(t_rng))};
}

static std::string generate_text(int len) {
	static const char kChars[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
	static constexpr size_t kNumChars = sizeof(kChars) - 1;
	thread_local std::mt19937 rng(std::random_device{}());
	std::uniform_int_distribution<size_t> dist(0, kNumChars - 1);
	std::string text;
	for (int i = 0; i < len; ++i) text.push_back(kChars[dist(rng)]);
	return text;
}

static inline uint8_t* pixel_at(std::vector<uint8_t>& pix, int x, int y) {
	return &pix[(y * kImgWidth + x) * kImgChannels];
}

static std::vector<uint8_t> rotate_bitmap(
	const FT_Bitmap& src, double angle_deg,
	int& out_w, int& out_h, int& offset_x, int& offset_y)
{
	double rad = angle_deg * M_PI / 180.0;
	double cos_a = std::cos(rad), sin_a = std::sin(rad);
	int sw = static_cast<int>(src.width), sh = static_cast<int>(src.rows);

	double corners[4][2] = {
		{0.0, 0.0}, {double(sw), 0.0}, {double(sw), double(sh)}, {0.0, double(sh)}
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
	offset_x = static_cast<int>(std::floor(min_x));
	offset_y = static_cast<int>(std::floor(min_y));

	std::vector<uint8_t> out(out_w * out_h, 0);
	for (int dy = 0; dy < out_h; ++dy) {
		for (int dx = 0; dx < out_w; ++dx) {
			double sx = (dx + min_x) * cos_a + (dy + min_y) * sin_a;
			double sy = -(dx + min_x) * sin_a + (dy + min_y) * cos_a;
			int six = static_cast<int>(std::floor(sx));
			int siy = static_cast<int>(std::floor(sy));
			if (six >= 0 && six < sw && siy >= 0 && siy < sh)
				out[dy * out_w + dx] = src.buffer[siy * src.pitch + six];
		}
	}
	return out;
}

static void composite_char(
	std::vector<uint8_t>& pixels,
	const std::vector<uint8_t>& glyph, int gw, int gh,
	int dst_x, int dst_y, const RGB& color)
{
	for (int gy = 0; gy < gh; ++gy) {
		for (int gx = 0; gx < gw; ++gx) {
			uint8_t alpha = glyph[gy * gw + gx];
			if (alpha == 0) continue;
			int px = dst_x + gx, py = dst_y + gy;
			if (px < 0 || px >= kImgWidth || py < 0 || py >= kImgHeight) continue;
			auto* p = pixel_at(pixels, px, py);
			float a = alpha / 255.0f;
			p[0] = static_cast<uint8_t>(p[0] * (1 - a) + color.r * a);
			p[1] = static_cast<uint8_t>(p[1] * (1 - a) + color.g * a);
			p[2] = static_cast<uint8_t>(p[2] * (1 - a) + color.b * a);
		}
	}
}

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

static void set_pixel(std::vector<uint8_t>& pixels, int x, int y, const RGB& color) {
	if (x >= 0 && x < kImgWidth && y >= 0 && y < kImgHeight) {
		auto* p = pixel_at(pixels, x, y);
		p[0] = color.r; p[1] = color.g; p[2] = color.b;
	}
}

static std::vector<uint8_t> generate_image(const std::string& text, const std::string& font_path) {
	std::vector<uint8_t> pixels(kImgWidth * kImgHeight * kImgChannels);
	auto bg = random_color();
	for (int i = 0; i < kImgWidth * kImgHeight; ++i) {
		pixels[i * 3 + 0] = bg.r;
		pixels[i * 3 + 1] = bg.g;
		pixels[i * 3 + 2] = bg.b;
	}

	FT_Library ft;
	if (FT_Init_FreeType(&ft) != 0) throw std::runtime_error("FreeType init failed");

	FT_Face face;
	if (FT_New_Face(ft, font_path.c_str(), 0, &face) != 0) {
		FT_Done_FreeType(ft);
		throw std::runtime_error("Font not found: " + font_path);
	}

	std::uniform_int_distribution<int> angle_dist(-30, 30);
	std::uniform_int_distribution<int> size_dist(20, 38);
	std::uniform_int_distribution<int> y_dist(2, 12);
	std::uniform_int_distribution<int> x_jitter(-3, 3);

	for (size_t i = 0; i < text.size(); ++i) {
		int char_size = size_dist(t_rng);
		FT_Set_Pixel_Sizes(face, 0, char_size);
		if (FT_Load_Char(face, text[i], FT_LOAD_RENDER) != 0) continue;

		auto& bmp = face->glyph->bitmap;
		if (bmp.buffer == nullptr || bmp.width == 0 || bmp.rows == 0) continue;

		auto fg = random_color();
		int angle = angle_dist(t_rng);
		int y = y_dist(t_rng);
		int x = 15 + static_cast<int>(i) * 32 + x_jitter(t_rng);

		int rw, rh, ox, oy;
		auto rotated = rotate_bitmap(bmp, angle, rw, rh, ox, oy);
		int cx = x - rw / 2;
		int cy = y;
		if (cy + rh > kImgHeight) cy = kImgHeight - rh;
		if (cy < 0) cy = 0;
		composite_char(pixels, rotated, rw, rh, cx, cy, fg);
	}

	FT_Done_Face(face);
	FT_Done_FreeType(ft);

	std::uniform_int_distribution<int> xd(0, kImgWidth - 1);
	std::uniform_int_distribution<int> yd(0, kImgHeight - 1);
	for (int i = 0; i < 4; ++i)
		draw_line(pixels, xd(t_rng), yd(t_rng), xd(t_rng), yd(t_rng), random_color());
	for (int i = 0; i < 50; ++i)
		set_pixel(pixels, xd(t_rng), yd(t_rng), random_color());

	std::vector<uint8_t> png_buf;
	stbi_write_png_to_func(
		[](void* ctx, void* data, int size) {
			auto* buf = static_cast<std::vector<uint8_t>*>(ctx);
			buf->insert(buf->end(), static_cast<uint8_t*>(data), static_cast<uint8_t*>(data) + size);
		}, &png_buf, kImgWidth, kImgHeight, kImgChannels, pixels.data(), kImgWidth * kImgChannels);
	return png_buf;
}

// ── main — 生成测试 PNG ──────────────────────────────────────────────────

int main(int argc, char* argv[]) {
	std::string font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf";
	std::string text = "AB3X";

	if (argc >= 2) text = argv[1];
	if (argc >= 3) font_path = argv[2];

	std::cout << "文字: " << text << std::endl;
	std::cout << "字体: " << font_path << std::endl;

	try {
		auto png = generate_image(text, font_path);
		std::string fname = "test_output_" + text + ".png";
		std::ofstream out(fname, std::ios::binary);
		out.write(reinterpret_cast<const char*>(png.data()), png.size());
		out.close();
		std::cout << "✓ 已生成: " << fname << " (" << png.size() << " bytes)" << std::endl;
	} catch (const std::exception& e) {
		std::cerr << "✗ 错误: " << e.what() << std::endl;
		return 1;
	}
	return 0;
}
