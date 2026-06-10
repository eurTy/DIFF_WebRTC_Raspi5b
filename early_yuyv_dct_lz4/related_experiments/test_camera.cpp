#include <iostream>
#include <fcntl.h>
#include <unistd.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

struct buffer {
    void* start;
    size_t length;
};

int main() {
    int fd = open("/dev/video0", O_RDWR);
    if(fd < 0){ perror("Cannot open camera"); return 1; }

    // 设置格式
    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = 640;
    fmt.fmt.pix.height = 480;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    fmt.fmt.pix.field = V4L2_FIELD_NONE;
    ioctl(fd, VIDIOC_S_FMT, &fmt);

    // 申请缓冲区
    v4l2_requestbuffers req{};
    req.count = 2;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    ioctl(fd, VIDIOC_REQBUFS, &req);

    buffer buffers[2];
    for(int i=0; i<2; ++i){
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        ioctl(fd, VIDIOC_QUERYBUF, &buf);
        buffers[i].length = buf.length;
        buffers[i].start = mmap(NULL, buf.length, PROT_READ|PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
        ioctl(fd, VIDIOC_QBUF, &buf);
    }

    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(fd, VIDIOC_STREAMON, &type);

    std::cout << "Starting frame capture..." << std::endl;
    for(int frame_count=0; frame_count<30; ++frame_count){
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        ioctl(fd, VIDIOC_DQBUF, &buf);

        std::cout << "Captured frame " << frame_count+1 << ", size: " << buf.bytesused << " bytes" << std::endl;

        ioctl(fd, VIDIOC_QBUF, &buf);
    }

    ioctl(fd, VIDIOC_STREAMOFF, &type);
    close(fd);
    std::cout << "Done." << std::endl;
    return 0;
}
