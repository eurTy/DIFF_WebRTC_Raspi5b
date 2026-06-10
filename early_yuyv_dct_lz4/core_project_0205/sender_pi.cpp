#include <iostream>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <chrono>
#include <opencv2/opencv.hpp>
#include "FastCodec.h"

// === 配置区 ===
#define TARGET_IP "192.168.71.93"
#define TARGET_PORT 5000
#define WIDTH 640
#define HEIGHT 480

struct PacketHeader {
    uint32_t frame_id;
    uint16_t start_row;
    uint16_t data_size;
};

int main() {
    // 1. 网络 Socket 初始化
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(TARGET_PORT);
    inet_pton(AF_INET, TARGET_IP, &addr.sin_addr);

    // 2. 摄像头初始化
    cv::VideoCapture cap(0, cv::CAP_V4L2);
    cap.set(cv::CAP_PROP_FRAME_WIDTH, WIDTH);
    cap.set(cv::CAP_PROP_FRAME_HEIGHT, HEIGHT);
    cap.set(cv::CAP_PROP_FPS, 60);

    if (!cap.isOpened()) {
        std::cerr << "错误：无法打开摄像头！" << std::endl;
        return -1;
    }

    cv::Mat frame, y_plane;
    uint32_t frame_id = 0;

    // 日志统计变量
    auto last_log_time = std::chrono::steady_clock::now();
    int total_bytes_sent = 0;
    int frames_count = 0;

    std::cout << "\033[1;34m[SYSTEM] 树莓派 5B 发送端启动...\033[0m" << std::endl;

    while (true) {
        cap >> frame;
        if (frame.empty()) continue;

        // 提取 Y 通道 (亮度)
        cv::extractChannel(frame, y_plane, 0);

        // 3. 并行处理与发送 (OpenMP)
        #pragma omp parallel for num_threads(4) reduction(+:total_bytes_sent)
        for (int r = 0; r < HEIGHT; r += 8) {
            cv::Mat strip = y_plane.rowRange(r, r + 8);
            char lz4_payload[1400];

            int c_size = FastCodec::EncodeStrip(strip, lz4_payload, 1400);

            // 封装包头
            PacketHeader head = { frame_id, (uint16_t)r, (uint16_t)c_size };
            char packet[1500];
            memcpy(packet, &head, sizeof(head));
            memcpy(packet + sizeof(head), lz4_payload, c_size);

            // 发送
            int sent = sendto(sock, packet, sizeof(head) + c_size, 0, (sockaddr*)&addr, sizeof(addr));
            if (sent > 0) total_bytes_sent += sent;
        }

        frames_count++;
        frame_id++;

        // 4. 每秒打印一次统计日志
        auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::seconds>(now - last_log_time).count() >= 1) {
            double mbps = (total_bytes_sent * 8.0) / (1024.0 * 1024.0);
            std::cout << "\r\033[1;32m[LOG]\033[0m Frame: " << frame_id
                      << " | FPS: " << frames_count
                      << " | Bandwidth: " << std::fixed << std::setprecision(2) << mbps << " Mbps"
                      << std::flush;

            total_bytes_sent = 0;
            frames_count = 0;
            last_log_time = now;
        }
    }

    close(sock);
    return 0;
}
