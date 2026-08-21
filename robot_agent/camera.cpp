#include "camera.hpp"

#include <jpeglib.h>

#include <fcntl.h>
#include <glob.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <stdexcept>

namespace {

constexpr size_t CAPTURE_BUFFER_COUNT = 3;
constexpr int FRAME_TIMEOUT_MS = 1500; // 单帧出队超时

// 扫描 /dev/video*，返回第一个支持视频采集的设备
std::string auto_detect_device() {
    glob_t g;
    std::vector<std::string> candidates;
    if (glob("/dev/video*", 0, nullptr, &g) == 0) {
        for (size_t i = 0; i < g.gl_pathc; ++i)
            candidates.emplace_back(g.gl_pathv[i]);
    }
    globfree(&g);
    std::sort(candidates.begin(), candidates.end());

    for (const auto& dev : candidates) {
        int fd = open(dev.c_str(), O_RDWR | O_NONBLOCK);
        if (fd < 0) continue;
        v4l2_capability cap{};
        bool ok = ioctl(fd, VIDIOC_QUERYCAP, &cap) == 0 &&
                  (cap.capabilities & V4L2_CAP_VIDEO_CAPTURE);
        close(fd);
        if (ok) return dev;
    }
    throw std::runtime_error("未找到可用的视频采集设备（已扫描 /dev/video*，"
                             "可用 --camera-device 指定）");
}

void xioctl(int fd, unsigned long request, void* arg, const char* what) {
    if (ioctl(fd, request, arg) != 0)
        throw std::runtime_error(std::string(what) + " 失败: " + strerror(errno));
}

// YUYV → RGB24 → JPEG（libjpeg 内存压缩）
std::vector<unsigned char> yuyv_to_jpeg(const std::vector<unsigned char>& yuyv,
                                        unsigned w, unsigned h) {
    std::vector<unsigned char> rgb(static_cast<size_t>(w) * h * 3);
    const unsigned char* in = yuyv.data();
    unsigned char* out = rgb.data();
    for (unsigned y = 0; y < h; ++y) {
        for (unsigned x = 0; x + 1 < w; x += 2) {
            int y0 = in[0], u = in[1] - 128, y1 = in[2], v = in[3] - 128;
            in += 4;
            int c0 = y0 - 16, c1 = y1 - 16;
            int r0 = (298 * c0 + 409 * v + 128) >> 8;
            int g0 = (298 * c0 - 100 * u - 208 * v + 128) >> 8;
            int b0 = (298 * c0 + 516 * u + 128) >> 8;
            int r1 = (298 * c1 + 409 * v + 128) >> 8;
            int g1 = (298 * c1 - 100 * u - 208 * v + 128) >> 8;
            int b1 = (298 * c1 + 516 * u + 128) >> 8;
            *out++ = static_cast<unsigned char>(std::clamp(r0, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp(g0, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp(b0, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp(r1, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp(g1, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp(b1, 0, 255));
        }
        if (w % 2) { // 奇数宽度：最后一像素复用前一对的 U/V
            int y0 = in[0], u = in[1] - 128, v = in[2] - 128;
            in += 4;
            int c0 = y0 - 16;
            *out++ = static_cast<unsigned char>(std::clamp((298 * c0 + 409 * v + 128) >> 8, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp((298 * c0 - 100 * u - 208 * v + 128) >> 8, 0, 255));
            *out++ = static_cast<unsigned char>(std::clamp((298 * c0 + 516 * u + 128) >> 8, 0, 255));
        }
    }

    jpeg_compress_struct cinfo;
    jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);
    unsigned char* outbuf = nullptr;
    unsigned long outsize = 0;
    jpeg_mem_dest(&cinfo, &outbuf, &outsize);
    cinfo.image_width = w;
    cinfo.image_height = h;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, 85, TRUE);
    jpeg_start_compress(&cinfo, TRUE);
    while (cinfo.next_scanline < h) {
        JSAMPROW row = rgb.data() + static_cast<size_t>(cinfo.next_scanline) * w * 3;
        jpeg_write_scanlines(&cinfo, &row, 1);
    }
    jpeg_finish_compress(&cinfo);
    std::vector<unsigned char> result(outbuf, outbuf + outsize);
    free(outbuf);
    jpeg_destroy_compress(&cinfo);
    return result;
}

} // namespace

CameraCapture::CameraCapture(const std::string& device) : device_(device) {
    if (device_.empty()) device_ = auto_detect_device();
    open_device();
    init_format();
    request_buffers();
}

CameraCapture::~CameraCapture() {
    std::lock_guard<std::mutex> lock(mtx_);
    if (fd_ < 0) return;
    try {
        stream_off();
    } catch (...) {
    }
    for (size_t i = 0; i < buf_count_; ++i)
        munmap(buffers_[i], buffer_sizes_[i]);
    close(fd_);
}

void CameraCapture::open_device() {
    fd_ = open(device_.c_str(), O_RDWR | O_NONBLOCK);
    if (fd_ < 0)
        throw std::runtime_error("无法打开摄像头 " + device_ + ": " + strerror(errno));

    v4l2_capability cap{};
    if (ioctl(fd_, VIDIOC_QUERYCAP, &cap) != 0)
        throw std::runtime_error("VIDIOC_QUERYCAP 失败: " + std::string(strerror(errno)));
    if (!(cap.capabilities & V4L2_CAP_VIDEO_CAPTURE) ||
        !(cap.capabilities & V4L2_CAP_STREAMING))
        throw std::runtime_error(device_ + " 不支持视频捕获或 mmap 流式传输");
}

void CameraCapture::init_format() {
    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = 640;
    fmt.fmt.pix.height = 480;
    fmt.fmt.pix.field = V4L2_FIELD_ANY;

    // 优先 MJPEG：摄像头硬件编码，抓帧即用，无需转码
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    if (ioctl(fd_, VIDIOC_S_FMT, &fmt) == 0 && fmt.fmt.pix.pixelformat == V4L2_PIX_FMT_MJPEG) {
        jpeg_format_ = true;
    } else {
        fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
        xioctl(fd_, VIDIOC_S_FMT, &fmt, "设置 YUYV 格式");
        if (fmt.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV)
            throw std::runtime_error(device_ + " 不支持 MJPEG 或 YUYV");
        jpeg_format_ = false;
    }
    width_ = fmt.fmt.pix.width;
    height_ = fmt.fmt.pix.height;
}

void CameraCapture::request_buffers() {
    v4l2_requestbuffers req{};
    req.count = CAPTURE_BUFFER_COUNT;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    xioctl(fd_, VIDIOC_REQBUFS, &req, "申请缓冲");
    if (req.count < 2)
        throw std::runtime_error("摄像头缓冲不足");
    buf_count_ = req.count;

    buffers_.resize(buf_count_);
    buffer_sizes_.resize(buf_count_);
    for (size_t i = 0; i < buf_count_; ++i) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        xioctl(fd_, VIDIOC_QUERYBUF, &buf, "查询缓冲");
        buffers_[i] = mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd_,
                           buf.m.offset);
        if (buffers_[i] == MAP_FAILED)
            throw std::runtime_error("mmap 失败: " + std::string(strerror(errno)));
        buffer_sizes_[i] = buf.length;
        xioctl(fd_, VIDIOC_QBUF, &buf, "缓冲入队");
    }
}

void CameraCapture::stream_on() {
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    xioctl(fd_, VIDIOC_STREAMON, &type, "开启视频流");
}

void CameraCapture::stream_off() {
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(fd_, VIDIOC_STREAMOFF, &type) != 0 && errno != EINVAL)
        throw std::runtime_error("关闭视频流失败: " + std::string(strerror(errno)));
}

std::vector<unsigned char> CameraCapture::dequeue_frame(int timeout_ms) {
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (true) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        if (ioctl(fd_, VIDIOC_DQBUF, &buf) == 0) {
            std::vector<unsigned char> frame(static_cast<unsigned char*>(buffers_[buf.index]),
                                             static_cast<unsigned char*>(buffers_[buf.index]) +
                                                 buf.bytesused);
            xioctl(fd_, VIDIOC_QBUF, &buf, "缓冲重新入队");
            return frame;
        }
        if (errno != EAGAIN)
            throw std::runtime_error("出队帧失败: " + std::string(strerror(errno)));
        auto now = std::chrono::steady_clock::now();
        if (now >= deadline)
            throw std::runtime_error("等待摄像头画面超时");
        int remain_ms = static_cast<int>(
            std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count());
        struct pollfd pfd{fd_, POLLIN, 0};
        poll(&pfd, 1, std::min(remain_ms, 200));
    }
}

std::string CameraCapture::grab_jpeg() {
    std::lock_guard<std::mutex> lock(mtx_);
    stream_on();
    try {
        dequeue_frame(FRAME_TIMEOUT_MS);                 // 丢弃第一帧（曝光未稳定）
        auto frame = dequeue_frame(FRAME_TIMEOUT_MS);    // 取第二帧
        stream_off();
        if (!jpeg_format_) {
            auto jpeg = yuyv_to_jpeg(frame, width_, height_);
            return std::string(reinterpret_cast<char*>(jpeg.data()), jpeg.size());
        }
        return std::string(reinterpret_cast<char*>(frame.data()), frame.size());
    } catch (...) {
        try {
            stream_off();
        } catch (...) {
        }
        throw;
    }
}
