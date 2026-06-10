#include <iostream>
#include <cstring>
#include <unistd.h>
#include <arpa/inet.h>
#include <chrono>

#pragma pack(push, 1)
struct UdpPacket {
    uint32_t frame_id;     // 帧序号
    uint64_t send_ts_us;   // 发送时间戳（微秒，树莓派本机）
};
#pragma pack(pop)

int main() {
    // 2️⃣ 创建 UDP socket
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) {
        perror("socket");
        return 1;
    }

    // 3️⃣ 配置目标（笔记本）
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_port = htons(5000);
    inet_pton(AF_INET, "192.168.71.93", &target.sin_addr);

    uint32_t frame_id = 0;

    while (true) {
        // 4️⃣ 构造数据包
        UdpPacket pkt{};
        pkt.frame_id = frame_id++;

        pkt.send_ts_us =
            std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now().time_since_epoch()
            ).count();

        // 5️⃣ 发送
        sendto(sock, &pkt, sizeof(pkt), 0,
               (sockaddr*)&target, sizeof(target));

        // 6️⃣ 接收回包（RTT）
        UdpPacket recv_pkt{};
        recvfrom(sock, &recv_pkt, sizeof(recv_pkt), 0, nullptr, nullptr);

        uint64_t now_us =
            std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now().time_since_epoch()
            ).count();

        double rtt_ms = (now_us - recv_pkt.send_ts_us) / 1000.0;
        double one_way_ms = rtt_ms / 2.0;

        std::cout
            << "Frame " << recv_pkt.frame_id
            << " | RTT " << rtt_ms << " ms"
            << " | One-way " << one_way_ms << " ms"
            << std::endl;

        usleep(30000); // 30 ms，避免刷屏
    }

    close(sock);
    return 0;
}
