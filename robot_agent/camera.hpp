#pragma once

#include <cstddef>
#include <mutex>
#include <string>
#include <vector>

// V4L2 摄像头抓帧：优先 MJPEG（硬件编码，抓到的就是 JPEG，零转码，速度最快），
// 设备不支持时回退 YUYV + libjpeg 软件编码。
// 设备常开复用（构造时打开并建流），每次抓帧 = STREAMON → 取帧 → STREAMOFF。
// device 为空时自动扫描 /dev/video* 探测第一个可用设备。失败抛 std::runtime_error。
class CameraCapture {
public:
    explicit CameraCapture(const std::string& device);
    ~CameraCapture();

    CameraCapture(const CameraCapture&) = delete;
    CameraCapture& operator=(const CameraCapture&) = delete;

    // 抓一帧，返回 JPEG 数据
    std::string grab_jpeg();

    unsigned width() const { return width_; }
    unsigned height() const { return height_; }

private:
    void open_device();
    void init_format();
    void request_buffers();
    void stream_on();
    void stream_off();
    // 出队一帧（带超时）。skip_first=true 时丢弃第一帧再取第二帧（等待曝光稳定）
    std::vector<unsigned char> dequeue_frame(int timeout_ms);

    std::string device_;
    int fd_ = -1;
    bool jpeg_format_ = false; // true = MJPEG 直出，false = YUYV 需转码
    unsigned width_ = 0;
    unsigned height_ = 0;
    std::vector<void*> buffers_;
    std::vector<size_t> buffer_sizes_;
    size_t buf_count_ = 0;
    std::mutex mtx_;
};
