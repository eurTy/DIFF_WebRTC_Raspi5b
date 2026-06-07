cmake_minimum_required(VERSION 3.10)
project(video_processor)

set(CMAKE_CXX_STANDARD 17)

add_executable(video_processor src/main.cpp)
target_link_libraries(video_processor pthread rt)
