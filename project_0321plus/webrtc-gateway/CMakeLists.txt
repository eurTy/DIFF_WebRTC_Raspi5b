cmake_minimum_required(VERSION 3.16)
project(webrtc_gateway)

set(CMAKE_CXX_STANDARD 17)

# 头文件路径
include_directories(/usr/local/include)

# 库文件路径
link_directories(/usr/local/lib)

add_executable(webrtc_gateway src/main.cpp)

target_link_libraries(webrtc_gateway
    datachannel
    gnutls
    nice
    srtp2
    usrsctp
    websockets   # 直接链接系统 libwebsockets.so
    pthread
    rt
)
