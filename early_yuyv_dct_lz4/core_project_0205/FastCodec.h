#ifndef FAST_CODEC_H
#define FAST_CODEC_H

#include <opencv2/opencv.hpp>
#include <lz4.h>
#include <vector>
#include <cstdint>

// 落地配置：为了极低延迟，我们牺牲一部分细节
#define KEEP_COEFFS 5      // 8x8块中只保留前10个低频系数
#define SCALE_FACTOR 16.0f  // 精度保留缩放

class FastCodec {
public:
    // ZigZag 扫描坐标映射 (y, x)
    static constexpr int ZIGZAG[10][2] = {
        {0,0}, {0,1}, {1,0}, {2,0}, {1,1}, {0,2}, {0,3}, {1,2}, {2,1}, {3,0}
    };

    // 编码：8行 Y 像素 -> 压缩 buffer
    static int EncodeStrip(const cv::Mat& strip_y, char* out_ptr, int max_size) {
        int width = strip_y.cols;
        std::vector<int16_t> coeffs;
        coeffs.reserve((width / 8) * KEEP_COEFFS);

        for (int x = 0; x < width; x += 8) {
            cv::Mat block_f, block_dct;
            // 提取 8x8 块并转为浮点
            strip_y(cv::Rect(x, 0, 8, 8)).convertTo(block_f, CV_32F);

            // 零中心化：减去128提高压缩效率
            block_f -= 128.0f;
            cv::dct(block_f, block_dct);

            // 按照 ZigZag 顺序提取前 10 个系数
            for (int i = 0; i < KEEP_COEFFS; i++) {
                float val = block_dct.at<float>(ZIGZAG[i][0], ZIGZAG[i][1]);
                coeffs.push_back((int16_t)(val * SCALE_FACTOR));
            }
        }

        // LZ4 压缩
        return LZ4_compress_default((char*)coeffs.data(), out_ptr, coeffs.size() * sizeof(int16_t), max_size);
    }
};

#endif
