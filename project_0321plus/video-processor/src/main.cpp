#include <iostream>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <opencv2/opencv.hpp>
#include <chrono>
#include <thread>

// 共享内存头部结构体
struct ShmHeader {
    uint32_t frame_id;
    uint32_t frag_total;
    uint32_t frag_size;
    uint32_t frame_size;
    uint32_t write_idx;
    uint32_t read_idx;
    uint32_t flags;          // 最低位：1=跳帧
    uint32_t motion_threshold;
    uint32_t keyframe_interval;
    uint64_t last_feedback_time;
};

const char* SHM_NAME = "/video_shm";
const size_t SHM_SIZE = 128 * 1024 * 1024;  // 128MB
const size_t HEADER_SIZE = sizeof(ShmHeader);
const size_t FRAGMENT_MAX_SIZE = 60 * 1024; // 60KB

float motionDetection(const cv::Mat& prev, const cv::Mat& curr) {
    cv::Mat diff, thresh;
    cv::absdiff(prev, curr, diff);
    cv::threshold(diff, thresh, 30, 255, cv::THRESH_BINARY);
    int nonZero = cv::countNonZero(thresh);
    return static_cast<float>(nonZero) / (thresh.rows * thresh.cols);
}

int main() {
    // 1. 打开摄像头（使用 OpenCV）
    cv::VideoCapture cap(0);
    if (!cap.isOpened()) {
        std::cerr << "Cannot open camera" << std::endl;
        return 1;
    }
    cap.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('M','J','P','G'));
    cap.set(cv::CAP_PROP_FRAME_WIDTH, 1280);
    cap.set(cv::CAP_PROP_FRAME_HEIGHT, 720);
    cap.set(cv::CAP_PROP_FPS, 30);

    // 2. 创建共享内存
    int shm_fd = shm_open(SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (shm_fd < 0) {
        std::cerr << "shm_open failed" << std::endl;
        return 1;
    }
    if (ftruncate(shm_fd, SHM_SIZE) < 0) {
        std::cerr << "ftruncate failed" << std::endl;
        return 1;
    }
    void* shm_ptr = mmap(nullptr, SHM_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
    if (shm_ptr == MAP_FAILED) {
        std::cerr << "mmap failed" << std::endl;
        return 1;
    }
    ShmHeader* header = static_cast<ShmHeader*>(shm_ptr);
    memset(header, 0, HEADER_SIZE);
    uint8_t* data_start = static_cast<uint8_t*>(shm_ptr) + HEADER_SIZE;

    // 3. 主循环
    cv::Mat frame, gray, prev_gray;
    uint32_t frame_id = 0;
    const float motion_threshold = 0.05f;
    const int keyframe_interval_sec = 30;
    const uint32_t keyframe_interval_frames = 60; // Debug-safe refresh: about 2 seconds at 30 fps.
    auto last_keyframe_time = std::chrono::steady_clock::now();

    while (true) {
        cap >> frame;
        if (frame.empty()) break;

        cv::cvtColor(frame, gray, cv::COLOR_BGR2GRAY);
        if (prev_gray.empty()) {
            prev_gray = gray.clone();
            continue;
        }
        float change_ratio = motionDetection(prev_gray, gray);
        prev_gray = gray.clone();

        auto now = std::chrono::steady_clock::now();
        bool is_periodic_keyframe = (frame_id <= 1) || (frame_id % keyframe_interval_frames == 0);
        bool is_timed_keyframe = std::chrono::duration_cast<std::chrono::seconds>(now - last_keyframe_time).count() >= keyframe_interval_sec;
        bool is_keyframe = is_periodic_keyframe ||
                           (change_ratio >= motion_threshold) ||
                           is_timed_keyframe;

        if (!is_keyframe) {
            // 跳帧
            header->frag_total = 0;
            header->frag_size = 0;
            header->frame_size = 0;
            header->flags = 1;
            header->motion_threshold = static_cast<uint32_t>(motion_threshold * 100);
            header->keyframe_interval = keyframe_interval_sec;
            header->last_feedback_time = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
            header->frame_id = frame_id;
            if (frame_id % keyframe_interval_frames == 0) {
                std::cout << "Skip frame " << frame_id << std::endl;
            }
        } else {
            // 编码为 JPEG
            std::vector<uchar> jpeg_buf;
            cv::imencode(".jpg", frame, jpeg_buf, {cv::IMWRITE_JPEG_QUALITY, 85});
            size_t data_len = jpeg_buf.size();
            uint32_t frag_total = (data_len + FRAGMENT_MAX_SIZE - 1) / FRAGMENT_MAX_SIZE;

            for (uint32_t i = 0; i < frag_total; ++i) {
                size_t start = i * FRAGMENT_MAX_SIZE;
                size_t end = std::min(start + FRAGMENT_MAX_SIZE, data_len);
                size_t frag_len = end - start;
                memcpy(data_start + i * FRAGMENT_MAX_SIZE, jpeg_buf.data() + start, frag_len);
            }

            header->frag_total = frag_total;
            header->frag_size = FRAGMENT_MAX_SIZE;
            header->frame_size = static_cast<uint32_t>(data_len);
            header->flags = 0;
            header->motion_threshold = static_cast<uint32_t>(motion_threshold * 100);
            header->keyframe_interval = keyframe_interval_sec;
            header->last_feedback_time = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
            header->frame_id = frame_id;
            std::cout << "Send keyframe " << frame_id << ", fragments=" << frag_total << std::endl;
            last_keyframe_time = now;
        }
        ++frame_id;
        std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }

    munmap(shm_ptr, SHM_SIZE);
    close(shm_fd);
    cap.release();
    return 0;
}
