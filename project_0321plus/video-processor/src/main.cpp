#include <algorithm>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <linux/videodev2.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <thread>
#include <unistd.h>
#include <vector>

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

struct Buffer {
    void* start = nullptr;
    size_t length = 0;
};

const char* SHM_NAME = "/video_shm";
const size_t SHM_SIZE = 128 * 1024 * 1024;
const size_t HEADER_SIZE = sizeof(ShmHeader);
const size_t FRAGMENT_MAX_SIZE = 24 * 1024;
const uint32_t LOG_INTERVAL_FRAMES = 30;

int xioctl(int fd, unsigned long request, void* arg) {
    int ret;
    do {
        ret = ioctl(fd, request, arg);
    } while (ret == -1 && errno == EINTR);
    return ret;
}

uint32_t envUInt(const char* name, uint32_t fallback) {
    const char* value = std::getenv(name);
    if (!value || !*value) {
        return fallback;
    }
    try {
        return static_cast<uint32_t>(std::stoul(value));
    } catch (...) {
        std::cerr << "[Config] Invalid " << name << "=" << value
                  << ", using " << fallback << std::endl;
        return fallback;
    }
}

std::string fourccToString(uint32_t fourcc) {
    std::string s(4, ' ');
    s[0] = static_cast<char>(fourcc & 0xFF);
    s[1] = static_cast<char>((fourcc >> 8) & 0xFF);
    s[2] = static_cast<char>((fourcc >> 16) & 0xFF);
    s[3] = static_cast<char>((fourcc >> 24) & 0xFF);
    return s;
}

bool setControl(int fd, uint32_t id, int32_t value, const std::string& name) {
    v4l2_control control{};
    control.id = id;
    control.value = value;
    if (xioctl(fd, VIDIOC_S_CTRL, &control) < 0) {
        std::cerr << "[Camera] Could not set " << name << "=" << value
                  << ": " << strerror(errno) << std::endl;
        return false;
    }
    std::cout << "[Camera] Set " << name << "=" << value << std::endl;
    return true;
}

void applyCameraControls(int fd) {
    setControl(fd, V4L2_CID_POWER_LINE_FREQUENCY, 1, "power_line_frequency_50hz");
    setControl(fd, V4L2_CID_EXPOSURE_AUTO_PRIORITY, 0, "exposure_dynamic_framerate");

    const uint32_t sharpness = envUInt("CAMERA_SHARPNESS", 4);
    setControl(fd, V4L2_CID_SHARPNESS, static_cast<int32_t>(sharpness), "sharpness");

    const char* exposure = std::getenv("CAMERA_EXPOSURE");
    if (exposure && *exposure) {
        setControl(fd, V4L2_CID_EXPOSURE_AUTO, 1, "auto_exposure_manual");
        setControl(fd, V4L2_CID_EXPOSURE_ABSOLUTE,
                   static_cast<int32_t>(envUInt("CAMERA_EXPOSURE", 157)),
                   "exposure_time_absolute");
    } else {
        setControl(fd, V4L2_CID_EXPOSURE_AUTO, 3, "auto_exposure_aperture_priority");
    }

    const char* gain = std::getenv("CAMERA_GAIN");
    if (gain && *gain) {
        setControl(fd, V4L2_CID_GAIN,
                   static_cast<int32_t>(envUInt("CAMERA_GAIN", 0)),
                   "gain");
    }

    const char* backlight = std::getenv("CAMERA_BACKLIGHT");
    if (backlight && *backlight) {
        setControl(fd, V4L2_CID_BACKLIGHT_COMPENSATION,
                   static_cast<int32_t>(envUInt("CAMERA_BACKLIGHT", 4)),
                   "backlight_compensation");
    }
}

int openCamera(const std::string& device, uint32_t width, uint32_t height, uint32_t fps) {
    int fd = open(device.c_str(), O_RDWR | O_NONBLOCK);
    if (fd < 0) {
        std::cerr << "[Camera] Cannot open " << device << ": " << strerror(errno) << std::endl;
        return -1;
    }

    v4l2_capability cap{};
    if (xioctl(fd, VIDIOC_QUERYCAP, &cap) < 0) {
        std::cerr << "[Camera] VIDIOC_QUERYCAP failed: " << strerror(errno) << std::endl;
        close(fd);
        return -1;
    }
    if (!(cap.capabilities & V4L2_CAP_VIDEO_CAPTURE) || !(cap.capabilities & V4L2_CAP_STREAMING)) {
        std::cerr << "[Camera] Device does not support capture streaming" << std::endl;
        close(fd);
        return -1;
    }

    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = width;
    fmt.fmt.pix.height = height;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    fmt.fmt.pix.field = V4L2_FIELD_ANY;
    if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) {
        std::cerr << "[Camera] VIDIOC_S_FMT MJPEG failed: " << strerror(errno) << std::endl;
        close(fd);
        return -1;
    }
    if (fmt.fmt.pix.pixelformat != V4L2_PIX_FMT_MJPEG) {
        std::cerr << "[Camera] Driver did not accept MJPEG, actual="
                  << fourccToString(fmt.fmt.pix.pixelformat) << std::endl;
        close(fd);
        return -1;
    }

    v4l2_streamparm parm{};
    parm.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    parm.parm.capture.timeperframe.numerator = 1;
    parm.parm.capture.timeperframe.denominator = fps;
    if (xioctl(fd, VIDIOC_S_PARM, &parm) < 0) {
        std::cerr << "[Camera] VIDIOC_S_PARM failed, continuing: "
                  << strerror(errno) << std::endl;
    }

    applyCameraControls(fd);

    v4l2_format actual{};
    actual.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_G_FMT, &actual) == 0) {
        std::cout << "[Camera] Format "
                  << actual.fmt.pix.width << "x" << actual.fmt.pix.height
                  << " " << fourccToString(actual.fmt.pix.pixelformat)
                  << ", sizeimage=" << actual.fmt.pix.sizeimage << std::endl;
    }

    v4l2_streamparm actualParm{};
    actualParm.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_G_PARM, &actualParm) == 0 &&
        actualParm.parm.capture.timeperframe.numerator != 0) {
        double actualFps =
            static_cast<double>(actualParm.parm.capture.timeperframe.denominator) /
            actualParm.parm.capture.timeperframe.numerator;
        std::cout << "[Camera] FPS " << actualFps << std::endl;
    }

    return fd;
}

std::vector<Buffer> initMmapBuffers(int fd) {
    v4l2_requestbuffers req{};
    req.count = 4;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_REQBUFS, &req) < 0) {
        std::cerr << "[Camera] VIDIOC_REQBUFS failed: " << strerror(errno) << std::endl;
        return {};
    }
    if (req.count < 2) {
        std::cerr << "[Camera] Insufficient mmap buffers" << std::endl;
        return {};
    }

    std::vector<Buffer> buffers(req.count);
    for (uint32_t i = 0; i < req.count; ++i) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        if (xioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) {
            std::cerr << "[Camera] VIDIOC_QUERYBUF failed: " << strerror(errno) << std::endl;
            return {};
        }
        buffers[i].length = buf.length;
        buffers[i].start = mmap(nullptr, buf.length, PROT_READ | PROT_WRITE,
                                MAP_SHARED, fd, buf.m.offset);
        if (buffers[i].start == MAP_FAILED) {
            std::cerr << "[Camera] buffer mmap failed: " << strerror(errno) << std::endl;
            return {};
        }
    }

    for (uint32_t i = 0; i < buffers.size(); ++i) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        if (xioctl(fd, VIDIOC_QBUF, &buf) < 0) {
            std::cerr << "[Camera] VIDIOC_QBUF failed: " << strerror(errno) << std::endl;
            return {};
        }
    }
    return buffers;
}

bool startStreaming(int fd) {
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) {
        std::cerr << "[Camera] VIDIOC_STREAMON failed: " << strerror(errno) << std::endl;
        return false;
    }
    return true;
}

void stopStreaming(int fd) {
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    xioctl(fd, VIDIOC_STREAMOFF, &type);
}

bool waitForFrame(int fd) {
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(fd, &fds);
    timeval tv{};
    tv.tv_sec = 2;
    tv.tv_usec = 0;
    int ret = select(fd + 1, &fds, nullptr, nullptr, &tv);
    if (ret < 0 && errno != EINTR) {
        std::cerr << "[Camera] select failed: " << strerror(errno) << std::endl;
    }
    return ret > 0;
}

bool looksLikeJpeg(const uint8_t* data, size_t len) {
    return len >= 4 && data[0] == 0xFF && data[1] == 0xD8;
}

void publishFrame(ShmHeader* header,
                  uint8_t* dataStart,
                  const uint8_t* frameData,
                  size_t frameSize,
                  uint32_t frameId,
                  uint32_t& writeSeq) {
    const uint32_t fragTotal =
        static_cast<uint32_t>((frameSize + FRAGMENT_MAX_SIZE - 1) / FRAGMENT_MAX_SIZE);

    header->write_idx = (writeSeq << 1) | 1U;
    std::atomic_thread_fence(std::memory_order_release);

    std::memcpy(dataStart, frameData, frameSize);

    header->frag_total = fragTotal;
    header->frag_size = static_cast<uint32_t>(FRAGMENT_MAX_SIZE);
    header->frame_size = static_cast<uint32_t>(frameSize);
    header->flags = 0;
    header->motion_threshold = 0;
    header->keyframe_interval = 0;
    header->last_feedback_time =
        static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                  std::chrono::steady_clock::now().time_since_epoch())
                                  .count());
    header->frame_id = frameId;

    std::atomic_thread_fence(std::memory_order_release);
    ++writeSeq;
    header->write_idx = writeSeq << 1;
}

int main() {
    const std::string device = std::getenv("CAMERA_DEVICE") ?
        std::getenv("CAMERA_DEVICE") : "/dev/video0";
    const uint32_t width = envUInt("CAMERA_WIDTH", 1280);
    const uint32_t height = envUInt("CAMERA_HEIGHT", 720);
    const uint32_t fps = envUInt("CAMERA_FPS", 10);

    std::cout << "[VideoProcessor] Native MJPEG capture starting: "
              << device << " " << width << "x" << height << "@" << fps << "fps" << std::endl;

    int cameraFd = openCamera(device, width, height, fps);
    if (cameraFd < 0) {
        return 1;
    }

    std::vector<Buffer> buffers = initMmapBuffers(cameraFd);
    if (buffers.empty()) {
        close(cameraFd);
        return 1;
    }
    if (!startStreaming(cameraFd)) {
        close(cameraFd);
        return 1;
    }

    int shmFd = shm_open(SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (shmFd < 0) {
        std::cerr << "[SHM] shm_open failed: " << strerror(errno) << std::endl;
        stopStreaming(cameraFd);
        close(cameraFd);
        return 1;
    }
    if (ftruncate(shmFd, SHM_SIZE) < 0) {
        std::cerr << "[SHM] ftruncate failed: " << strerror(errno) << std::endl;
        close(shmFd);
        stopStreaming(cameraFd);
        close(cameraFd);
        return 1;
    }
    void* shmPtr = mmap(nullptr, SHM_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, shmFd, 0);
    if (shmPtr == MAP_FAILED) {
        std::cerr << "[SHM] mmap failed: " << strerror(errno) << std::endl;
        close(shmFd);
        stopStreaming(cameraFd);
        close(cameraFd);
        return 1;
    }

    ShmHeader* header = static_cast<ShmHeader*>(shmPtr);
    std::memset(header, 0, HEADER_SIZE);
    uint8_t* dataStart = static_cast<uint8_t*>(shmPtr) + HEADER_SIZE;

    uint32_t frameId = 0;
    uint32_t writeSeq = 0;
    uint32_t invalidJpegCount = 0;
    const size_t maxFrameSize = SHM_SIZE - HEADER_SIZE;

    while (true) {
        if (!waitForFrame(cameraFd)) {
            std::cerr << "[Camera] Timeout waiting for frame" << std::endl;
            continue;
        }

        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        if (xioctl(cameraFd, VIDIOC_DQBUF, &buf) < 0) {
            if (errno == EAGAIN) {
                continue;
            }
            std::cerr << "[Camera] VIDIOC_DQBUF failed: " << strerror(errno) << std::endl;
            break;
        }

        const uint8_t* frameData = static_cast<const uint8_t*>(buffers[buf.index].start);
        const size_t frameSize = buf.bytesused;
        if (frameSize > 0 && frameSize <= maxFrameSize) {
            if (!looksLikeJpeg(frameData, frameSize)) {
                ++invalidJpegCount;
                if (invalidJpegCount % LOG_INTERVAL_FRAMES == 1) {
                    std::cerr << "[VideoProcessor] Captured frame does not look like JPEG, size="
                              << frameSize << std::endl;
                }
            } else {
                publishFrame(header, dataStart, frameData, frameSize, frameId, writeSeq);
                if (frameId % LOG_INTERVAL_FRAMES == 0) {
                    const uint32_t fragTotal =
                        static_cast<uint32_t>((frameSize + FRAGMENT_MAX_SIZE - 1) / FRAGMENT_MAX_SIZE);
                    std::cout << "[VideoProcessor] Send native MJPEG frame " << frameId
                              << ", bytes=" << frameSize
                              << ", fragments=" << fragTotal << std::endl;
                }
                ++frameId;
            }
        } else {
            std::cerr << "[VideoProcessor] Invalid frame size=" << frameSize << std::endl;
        }

        if (xioctl(cameraFd, VIDIOC_QBUF, &buf) < 0) {
            std::cerr << "[Camera] VIDIOC_QBUF failed: " << strerror(errno) << std::endl;
            break;
        }
    }

    munmap(shmPtr, SHM_SIZE);
    close(shmFd);
    stopStreaming(cameraFd);
    for (const auto& buffer : buffers) {
        if (buffer.start && buffer.start != MAP_FAILED) {
            munmap(buffer.start, buffer.length);
        }
    }
    close(cameraFd);
    return 0;
}
