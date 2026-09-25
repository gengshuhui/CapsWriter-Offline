# =============================================================================
# CapsWriter-Offline Linux 跨架构打包 Dockerfile
# 用法 (在 ARM64 主机上产出 x86_64 产物):
#   docker buildx create --name multiarch --use   # 一次性
#   docker buildx build --platform linux/amd64 \
#       -f scripts/linux/Dockerfile --output type=local,dest=dist-x86_64 .
#
# 用法 (产出 ARM64 产物):
#   docker buildx build --platform linux/arm64 \
#       -f scripts/linux/Dockerfile --output type=local,dest=dist-aarch64 .
# =============================================================================
FROM --platform=$BUILDPLATFORM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-dev python3-venv python3-tk python3-pip \
    libportaudio2 libportaudiocpp0 libsndfile1 \
    libxcb-cursor0 libnotify-bin \
    ffmpeg xclip xsel \
    ca-certificates curl wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 复制源码
COPY . /build

# 在容器内运行一键打包脚本 (它会自己创建 venv 并安装依赖)
# 注意: 这会下载 ~30 MB 的 llama.cpp 二进制
RUN bash scripts/linux/package_linux.sh --no-venv --skip-models
# ^ 跳过 llama.cpp 二进制下载, 改为由用户在产物中手动放置
# 或者: 去掉 --skip-models, 让脚本自动下载

# 验证产物
RUN file dist/CapsWriter-Offline/start_server \
    && file dist/CapsWriter-Offline/start_client \
    && du -sh dist/CapsWriter-Offline

# 默认输出整个 dist/CapsWriter-Offline/ 目录
# 调用方用 --output type=local,dest=... 指定输出位置
