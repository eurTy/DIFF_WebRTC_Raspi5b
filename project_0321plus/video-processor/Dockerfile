# 使用 ARM64 Ubuntu 22.04 作为基础镜像
FROM arm64v8/ubuntu:22.04

# 设置非交互式安装，避免 tzdata 等弹出配置界面
ENV DEBIAN_FRONTEND=noninteractive

# 更换国内源（可选，加速下载）
RUN sed -i 's/ports.ubuntu.com/mirrors.aliyun.com/g' /etc/apt/sources.list

# 安装编译工具和依赖库
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopencv-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建工作目录
WORKDIR /app

# 复制源代码
COPY CMakeLists.txt .
COPY src/ ./src/

# 编译程序
RUN mkdir build && cd build && cmake .. && make

# 设置运行时路径（如果使用动态库，但 OpenCV 已安装到系统路径，无需额外设置）
# CMD 运行编译好的可执行文件
CMD ["./build/video_processor"]
