#include <iostream>
#include <memory>
#include <thread>
#include <chrono>
#include <string>
#include <cstring>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <arpa/inet.h>
#include <libwebsockets.h>
#include <rtc/rtc.hpp>
#include <nlohmann/json.hpp>
#include <functional>
#include <atomic>
#include <mutex>
#include <queue>
#include <cerrno>
#include <cstddef>
#include <vector>
#include <algorithm>

using json = nlohmann::json;

// ==================== 共享内存结构 ====================
struct ShmHeader {
    uint32_t frame_id;
    uint32_t frag_total;
    uint32_t frag_size;
    uint32_t frame_size;
    uint32_t write_idx;
    uint32_t read_idx;
    uint32_t flags;
    uint32_t motion_threshold;
    uint32_t keyframe_interval;
    uint64_t last_feedback_time;
};

const char* SHM_NAME = "/video_shm";
const size_t SHM_SIZE = 128 * 1024 * 1024;
const size_t HEADER_SIZE = sizeof(ShmHeader);
const size_t FRAGMENT_MAX_SIZE = 60 * 1024;

struct __attribute__((packed)) VideoFragmentHeader {
    uint32_t magic;
    uint32_t frame_id;
    uint16_t frag_idx;
    uint16_t frag_total;
    uint32_t payload_len;
};

const uint32_t VIDEO_FRAGMENT_MAGIC = 0x56504631; // "VPF1"

// ==================== WebSocket 信令客户端 ====================
static struct lws_context* context = nullptr;
static struct lws* wsi = nullptr;
static std::string incomingMessage;
static std::function<void(const std::string&)> onMessageCallback;
static std::mutex msgMutex;
static std::atomic<bool> signalingConnected{false};
static std::atomic<bool> running{true};

// 发送队列
std::queue<std::string> sendQueue;
std::mutex sendMutex;

// 发送信令消息（线程安全）
bool sendSignalingMessage(const std::string& msg) {
    std::cout << "[Signaling] sendSignalingMessage called, msg size=" << msg.size() << std::endl;
    if (!signalingConnected) {
        std::cerr << "[Signaling] sendSignalingMessage: signalingConnected false" << std::endl;
        return false;
    }
    if (!wsi) {
        std::cerr << "[Signaling] sendSignalingMessage: wsi is null" << std::endl;
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(sendMutex);
        sendQueue.push(msg);
    }
    lws_callback_on_writable(wsi);
    std::cout << "[Signaling] sendSignalingMessage: queued, requested writable" << std::endl;
    return true;
}

// WebSocket 回调
static int callback_ws(struct lws* wsi, enum lws_callback_reasons reason,
                       void* user, void* in, size_t len) {
    switch (reason) {
        case LWS_CALLBACK_CLIENT_ESTABLISHED:
            std::cout << "[Signaling] Connected to server" << std::endl;
            signalingConnected = true;
            lws_callback_on_writable(wsi);
            break;

        case LWS_CALLBACK_CLIENT_RECEIVE: {
            std::lock_guard<std::mutex> lock(msgMutex);
            incomingMessage.append((char*)in, len);
            if (lws_is_final_fragment(wsi) && lws_remaining_packet_payload(wsi) == 0) {
                if (onMessageCallback) {
                    onMessageCallback(incomingMessage);
                }
                incomingMessage.clear();
            }
            break;
        }

        case LWS_CALLBACK_CLIENT_WRITEABLE: {
            std::cout << "[Signaling] WRITEABLE callback triggered" << std::endl;
            std::lock_guard<std::mutex> lock(sendMutex);
            if (!sendQueue.empty()) {
                std::string msg = sendQueue.front();
                std::cout << "[Signaling] Sending message (len=" << msg.size() << "): " 
                          << msg.substr(0, 80) << (msg.size() > 80 ? "..." : "") << std::endl;
                std::vector<uint8_t> buffer(LWS_PRE + msg.size());
                memcpy(&buffer[LWS_PRE], msg.c_str(), msg.size());
                int ret = lws_write(wsi, &buffer[LWS_PRE], msg.size(), LWS_WRITE_TEXT);
                if (ret < 0) {
                    std::cerr << "[Signaling] lws_write failed, ret=" << ret << std::endl;
                } else {
                    std::cout << "[Signaling] lws_write succeeded, sent " << ret << " bytes" << std::endl;
                    sendQueue.pop();
                }
                if (!sendQueue.empty()) {
                    lws_callback_on_writable(wsi);
                }
            } else {
                std::cout << "[Signaling] WRITEABLE callback but queue empty" << std::endl;
            }
            break;
        }

        case LWS_CALLBACK_CLIENT_CLOSED:
            std::cout << "[Signaling] Disconnected from server" << std::endl;
            signalingConnected = false;
            wsi = nullptr;
            break;

        case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
            std::cerr << "[Signaling] Connection failed" << std::endl;
            signalingConnected = false;
            wsi = nullptr;
            break;

        default:
            break;
    }
    return 0;
}

static struct lws_protocols protocols[] = {
    { "ws", callback_ws, 0, 65536 },
    { nullptr, nullptr, 0, 0 }
};

void signalingLoop() {
    while (running) {
        lws_service(context, 50);
    }
    if (context) {
        lws_context_destroy(context);
        context = nullptr;
    }
}

void connectSignaling(const std::string& serverIp, int port,
                      std::function<void(const std::string&)> callback) {
    onMessageCallback = std::move(callback);

    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    info.port = CONTEXT_PORT_NO_LISTEN;
    info.protocols = protocols;
    info.gid = -1;
    info.uid = -1;
    info.options = LWS_SERVER_OPTION_DO_SSL_GLOBAL_INIT;

    context = lws_create_context(&info);
    if (!context) {
        std::cerr << "[Error] lws_create_context failed" << std::endl;
        return;
    }

    struct lws_client_connect_info ccinfo = {0};
    ccinfo.context = context;
    ccinfo.address = serverIp.c_str();
    ccinfo.port = port;
    ccinfo.path = "/?role=gateway";
    ccinfo.host = lws_canonical_hostname(context);
    ccinfo.origin = "origin";
    ccinfo.protocol = protocols[0].name;
    ccinfo.ssl_connection = 0;

    wsi = lws_client_connect_via_info(&ccinfo);
    if (!wsi) {
        std::cerr << "[Error] lws_client_connect failed" << std::endl;
        lws_context_destroy(context);
        context = nullptr;
        return;
    }

    std::thread(signalingLoop).detach();
}

// ==================== WebRTC 核心逻辑 ====================
std::shared_ptr<rtc::PeerConnection> peerConnection;
std::shared_ptr<rtc::DataChannel> reliableChannel;
std::shared_ptr<rtc::DataChannel> unreliableChannel;

void setupWebRTC() {
    rtc::InitLogger(rtc::LogLevel::Debug);

    rtc::Configuration config;
    config.iceServers.emplace_back("stun:stun.l.google.com:19302");

    peerConnection = std::make_shared<rtc::PeerConnection>(config);
    std::cout << "[WebRTC] PeerConnection created" << std::endl;

    // 先设置回调（重要：避免错过内部协商事件）
    peerConnection->onLocalDescription([&](const rtc::Description& sdp) {
        std::cout << "[WebRTC] Generated local SDP" << std::endl;
        if (signalingConnected) {
            std::cout << "[WebRTC] signalingConnected true, preparing to send SDP..." << std::endl;
            json j;
            j["type"] = (sdp.type() == rtc::Description::Type::Offer) ? "offer" : "answer";
            j["sdp"] = std::string(sdp);
            std::string msg = j.dump() + "\n";
            sendSignalingMessage(msg);
        } else {
            std::cerr << "[WebRTC] signalingConnected false, cannot send SDP" << std::endl;
        }
    });

    peerConnection->onLocalCandidate([&](const rtc::Candidate& candidate) {
        std::cout << "[WebRTC] Generated local ICE candidate" << std::endl;
        if (signalingConnected) {
            json j;
            j["candidate"] = std::string(candidate);
            j["sdpMid"] = candidate.mid();
            sendSignalingMessage(j.dump() + "\n");
        }
    });

    peerConnection->onStateChange([](rtc::PeerConnection::State state) {
        std::cout << "[WebRTC] Connection state: " << state << std::endl;
    });

    // 创建 DataChannel（这会触发内部协商，从而调用 onLocalDescription）
    rtc::DataChannelInit reliableConfig;
    reliableConfig.reliability.unordered = false;
    rtc::DataChannelInit unreliableConfig;
    unreliableConfig.reliability.unordered = true;
    unreliableConfig.reliability.maxRetransmits = 0;

    reliableChannel = peerConnection->createDataChannel("control", reliableConfig);
    unreliableChannel = peerConnection->createDataChannel("video", unreliableConfig);
    std::cout << "[WebRTC] DataChannels created" << std::endl;

    reliableChannel->onOpen([]() { std::cout << "[WebRTC] Reliable channel opened" << std::endl; });
    unreliableChannel->onOpen([]() { std::cout << "[WebRTC] Unreliable channel opened" << std::endl; });
}

void handleSignalingMessage(const std::string& msg) {
    try {
        json j = json::parse(msg);
        if (j.contains("type") && j.contains("sdp")) {
            std::string sdpStr = j["sdp"].get<std::string>();
            std::string typeStr = j["type"].get<std::string>();
            std::cout << "[Signaling] Received remote SDP (" << typeStr << ")" << std::endl;
            rtc::Description sdp(sdpStr, typeStr);
            peerConnection->setRemoteDescription(sdp);
        } else if (j.contains("candidate")) {
            std::string cand = j["candidate"].get<std::string>();
            std::string sdpMid = j["sdpMid"].get<std::string>();
            std::cout << "[Signaling] Received remote ICE candidate" << std::endl;
            rtc::Candidate candidate(cand, sdpMid);
            peerConnection->addRemoteCandidate(candidate);
        } else {
            std::cerr << "[Signaling] Unknown message type: " << msg << std::endl;
        }
    } catch (const std::exception& e) {
        std::cerr << "[Signaling] Parse error: " << e.what() << std::endl;
    }
}

// ==================== 主函数 ====================
int main() {
    rtc::InitLogger(rtc::LogLevel::Debug);
    std::cout << "WebRTC Gateway Starting..." << std::endl;

    const std::string signalingIp = "127.0.0.1";
    const int signalingPort = 8080;

    connectSignaling(signalingIp, signalingPort, handleSignalingMessage);

    int waitCount = 0;
    while (!signalingConnected && waitCount < 100) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        ++waitCount;
    }

    if (signalingConnected) {
        std::cout << "[Main] Signaling connected, initializing WebRTC..." << std::endl;
        setupWebRTC();
    } else {
        std::cerr << "[Main] Signaling connection timeout, exiting" << std::endl;
        running = false;
        return 1;
    }

    // ========== 新增：共享内存读取和分片发送 ==========
    // PC/手机端可能晚于网关打开，保持等待而不是退出重启。
    int waitChannel = 0;
    while (running && (!reliableChannel || !unreliableChannel || !unreliableChannel->isOpen())) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        ++waitChannel;
        if (waitChannel % 50 == 0) {
            std::cout << "[Main] Waiting for WebRTC video channel..." << std::endl;
        }
    }
    if (!running) {
        return 1;
    }
    std::cout << "[Main] Unreliable channel opened, starting video streaming..." << std::endl;

    // 打开共享内存
    int shm_fd = shm_open(SHM_NAME, O_RDWR, 0666);
    if (shm_fd < 0) {
        std::cerr << "[Main] shm_open failed: " << strerror(errno) << std::endl;
        return 1;
    }
    void* shm_ptr = mmap(nullptr, SHM_SIZE, PROT_READ, MAP_SHARED, shm_fd, 0);
    if (shm_ptr == MAP_FAILED) {
        std::cerr << "[Main] mmap failed: " << strerror(errno) << std::endl;
        close(shm_fd);
        return 1;
    }
    ShmHeader* header = static_cast<ShmHeader*>(shm_ptr);
    uint8_t* data_start = static_cast<uint8_t*>(shm_ptr) + HEADER_SIZE;
    std::cout << "[Main] Shared memory mapped successfully" << std::endl;

    uint32_t last_frame_id = static_cast<uint32_t>(-1);
    auto last_skip_control_time = std::chrono::steady_clock::now() - std::chrono::seconds(2);
    const auto skip_control_interval = std::chrono::milliseconds(1000);
    const uint32_t skip_log_interval_frames = 60;

    while (running) {
        ShmHeader h = *header;
        if (h.frame_id != last_frame_id) {
            const bool is_skip_frame = (h.flags & 1) != 0;
            if (!is_skip_frame || h.frame_id % skip_log_interval_frames == 0) {
                std::cout << "[Gateway] New frame detected: id=" << h.frame_id
                          << ", flags=" << h.flags
                          << ", fragments=" << h.frag_total
                          << ", bytes=" << h.frame_size << std::endl;
            }

            if (is_skip_frame) {
                const auto now = std::chrono::steady_clock::now();
                if (now - last_skip_control_time >= skip_control_interval) {
                    json j;
                    j["type"] = "skip_frame";
                    j["frame_id"] = h.frame_id;
                    reliableChannel->send(j.dump());
                    last_skip_control_time = now;
                }
            } else {
                if (h.frame_size == 0 || h.frame_size > SHM_SIZE - HEADER_SIZE || h.frag_size == 0) {
                    std::cerr << "[Gateway] Invalid frame metadata, dropping frame " << h.frame_id << std::endl;
                    last_frame_id = h.frame_id;
                    continue;
                }

                uint32_t expected_fragments = (h.frame_size + FRAGMENT_MAX_SIZE - 1) / FRAGMENT_MAX_SIZE;
                if (h.frag_total != expected_fragments) {
                    std::cerr << "[Gateway] Fragment count mismatch, header=" << h.frag_total
                              << ", expected=" << expected_fragments << std::endl;
                    last_frame_id = h.frame_id;
                    continue;
                }

                // 关键帧：发送元数据
                json start;
                start["type"] = "frame_start";
                start["frame_id"] = h.frame_id;
                start["total_fragments"] = h.frag_total;
                start["total_bytes"] = h.frame_size;
                reliableChannel->send(start.dump());

                // 发送每个分片
                for (uint32_t i = 0; i < h.frag_total; ++i) {
                    size_t offset = i * FRAGMENT_MAX_SIZE;
                    size_t remaining = h.frame_size - offset;
                    size_t len = std::min(FRAGMENT_MAX_SIZE, remaining);
                    VideoFragmentHeader fragmentHeader;
                    fragmentHeader.magic = htonl(VIDEO_FRAGMENT_MAGIC);
                    fragmentHeader.frame_id = htonl(h.frame_id);
                    fragmentHeader.frag_idx = htons(static_cast<uint16_t>(i));
                    fragmentHeader.frag_total = htons(static_cast<uint16_t>(h.frag_total));
                    fragmentHeader.payload_len = htonl(static_cast<uint32_t>(len));

                    std::vector<std::byte> packet(sizeof(VideoFragmentHeader) + len);
                    memcpy(packet.data(), &fragmentHeader, sizeof(VideoFragmentHeader));
                    memcpy(packet.data() + sizeof(VideoFragmentHeader), data_start + offset, len);

                    std::cout << "[Gateway] Sending fragment " << i << "/" << h.frag_total
                              << ", size=" << len << std::endl;
                    unreliableChannel->send(packet.data(), packet.size());
                }

                json end;
                end["type"] = "frame_end";
                end["frame_id"] = h.frame_id;
                reliableChannel->send(end.dump());
            }
            last_frame_id = h.frame_id;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    munmap(shm_ptr, SHM_SIZE);
    close(shm_fd);
    return 0;
}
