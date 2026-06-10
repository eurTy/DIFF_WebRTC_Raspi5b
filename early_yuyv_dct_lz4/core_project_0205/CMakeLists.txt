cmake_minimum_required(VERSION 3.10)
project(PiStreamer)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -fopenmp -march=native")

find_package(OpenCV REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(LZ4 REQUIRED liblz4)

include_directories(${OpenCV_INCLUDE_DIRS} include ${LZ4_INCLUDE_DIRS})
add_executable(sender sender_pi.cpp)
target_link_libraries(sender ${OpenCV_LIBS} ${LZ4_LIBRARIES})
