#include <iostream>
#include <fcntl.h>
#include <unistd.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <jpeglib.h>
#include <cstring>

#define WIDTH 640
#define HEIGHT 480
#define DEVICE "/dev/video0"
#define UDP_IP "192.168.71.93"
#define UDP_PORT 5000

struct buffer {
    void* start;
    size_t length;
};

int main() {
    int fd = open(DEVICE, O_RDWR);
    if(fd < 0) { perror("Cannot open device"); return 1; }

    // 设置格式 MJPG 640x480
    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = WIDTH;
    fmt.fmt.pix.height = HEIGHT;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
    fmt.fmt.pix.field = V4L2_FIELD_NONE;
    ioctl(fd, VIDIOC_S_FMT, &fmt);

    // 请求 buffer
    v4l2_requestbuffers req{};
    req.count = 2;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    ioctl(fd, VIDIOC_REQBUFS, &req);

    buffer buffers[req.count];
    for(int i=0; i<req.count; ++i) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        ioctl(fd, VIDIOC_QUERYBUF, &buf);
        buffers[i].length = buf.length;
        buffers[i].start = mmap(NULL, buf.length, PROT_READ|PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
        ioctl(fd, VIDIOC_QBUF, &buf);
    }

    // 开始采集
    int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(fd, VIDIOC_STREAMON, &type);

    // UDP 初始化
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(UDP_PORT);
    inet_pton(AF_INET, UDP_IP, &addr.sin_addr);

    unsigned char* prev_gray = new unsigned char[WIDTH*HEIGHT];

    while(true){
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
        buf.memory = V4L2_MEMORY_MMAP;
        ioctl(fd, VIDIOC_DQBUF, &buf);

        // MJPG 解码成灰度
        jpeg_decompress_struct cinfo{};
        jpeg_error_mgr jerr{};
        cinfo.err = jpeg_std_error(&jerr);
        jpeg_create_decompress(&cinfo);
        jpeg_mem_src(&cinfo, (unsigned char*)buffers[buf.index].start, buf.bytesused);
        jpeg_read_header(&cinfo, TRUE);
        cinfo.out_color_space = JCS_GRAYSCALE;
        jpeg_start_decompress(&cinfo);
        unsigned char* gray = new unsigned char[cinfo.output_width*cinfo.output_height];
        while(cinfo.output_scanline < cinfo.output_height){
            unsigned char* rowptr[1] = { gray + cinfo.output_scanline*cinfo.output_width };
            jpeg_read_scanlines(&cinfo, rowptr, 1);
        }
        jpeg_finish_decompress(&cinfo);
        jpeg_destroy_decompress(&cinfo);

        // 计算变化域（帧差）
        for(int i=0; i<WIDTH*HEIGHT; ++i){
            gray[i] = prev_gray[i] ? abs(gray[i]-prev_gray[i]) : gray[i];
        }
        memcpy(prev_gray, gray, WIDTH*HEIGHT);

        // UDP 发送原始 MJPG 帧
        sendto(sock, buffers[buf.index].start, buf.bytesused, 0, (sockaddr*)&addr, sizeof(addr));

        delete[] gray;
        ioctl(fd, VIDIOC_QBUF, &buf);
    }

    delete[] prev_gray;
    ioctl(fd, VIDIOC_STREAMOFF, &type);
    close(fd);
    close(sock);
    return 0;
}
