FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# =============================
# 1. 安装依赖（最小稳定集合）
# =============================
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    pkg-config \
    nlohmann-json3-dev\
    libgnutls28-dev \
    libnice-dev \
    libsrtp2-dev \
    libusrsctp-dev \
    libwebsockets-dev\
    && rm -rf /var/lib/apt/lists/*

# =============================
# 2. 拷贝 libdatachannel 源码
# =============================
COPY libdatachannel /opt/libdatachannel

# =============================
# 3. 编译 libdatachannel（关键修复点：GnuTLS）
# =============================
RUN cmake -S /opt/libdatachannel -B /opt/libdatachannel/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DUSE_GNUTLS=ON \
    -DUSE_MBEDTLS=OFF \
    -DUSE_NICE=ON \
    -DNO_TESTS=ON \
    -DNO_EXAMPLES=ON && \
    cmake --build /opt/libdatachannel/build -j$(nproc) && \
    cmake --install /opt/libdatachannel/build && \
    ldconfig && \
    rm -rf /opt/libdatachannel

# =============================
# 4. 拷贝你的项目
# =============================
WORKDIR /app
COPY . .

# =============================
# 5. 编译 webrtc-gateway
# =============================
RUN cmake -S . -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/usr/local && \
    cmake --build build -j$(nproc)

# =============================
# 6. 运行
# =============================
CMD ["./build/webrtc_gateway"]
