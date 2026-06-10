#include <iostream>
#include <vector>
#include <cmath>

void test_frame_difference(const std::vector<unsigned char>& prev, const std::vector<unsigned char>& cur) {
    if(prev.size() != cur.size()) {
        std::cout << "Frame size mismatch!" << std::endl;
        return;
    }
    int changed_pixels = 0;
    for(size_t i = 0; i < prev.size(); ++i){
        if(std::abs(cur[i] - prev[i]) > 10) // 阈值10
            changed_pixels++;
    }
    std::cout << "Changed pixels: " << changed_pixels << std::endl;
}

int main() {
    // 假设两个模拟帧
    std::vector<unsigned char> frame1(640*480, 100); // 全灰色
    std::vector<unsigned char> frame2(640*480, 100);
    frame2[1000] = 200; // 模拟变化

    test_frame_difference(frame1, frame2);
    return 0;
}
