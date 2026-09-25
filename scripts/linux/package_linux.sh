#!/usr/bin/env bash
# =============================================================================
# CapsWriter-Offline Linux 一键打包脚本
# =============================================================================
# 作用:
#   1. 自动检测架构 (aarch64 / x86_64)
#   2. 创建独立 venv (避免污染系统 Python)
#   3. 安装 Linux 平台适配的依赖 (替换 onnxruntime-directml 为 onnxruntime)
#   4. 下载 llama.cpp Linux 二进制 (用于 GGUF LLM 推理)
#   5. 调用 PyInstaller 生成 dist/CapsWriter-Offline/ 目录
#
# 用法:
#   ./package_linux.sh                # 完整打包 (server + client)
#   ./package_linux.sh --client-only  # 仅打包客户端
#   ./package_linux.sh --no-venv      # 跳过 venv 创建 (使用当前 python3)
#   ./package_linux.sh --skip-models  # 不下载 llama.cpp 二进制 (server 必须另外下载)
#   ./package_linux.sh --help
#
# 输出:
#   dist/CapsWriter-Offline/   或   dist/CapsWriter-Offline-Client/
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# 路径与配置
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# 颜色
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err()  { echo -e "${RED}[FAIL]${NC} $*"; }

# -----------------------------------------------------------------------------
# 参数解析
# -----------------------------------------------------------------------------
CLIENT_ONLY=false
USE_VENV=true
SKIP_MODELS=false
ARCH_FORCE=""
PYTHON_BIN=""
FIX_PERMS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --client-only)    CLIENT_ONLY=true; shift ;;
        --no-venv)        USE_VENV=false; shift ;;
        --skip-models)    SKIP_MODELS=true; shift ;;
        --fix-permissions) FIX_PERMS=true; shift ;;
        --python)         PYTHON_BIN="$2"; shift 2 ;;
        --arch)           ARCH_FORCE="$2"; shift 2 ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --client-only     仅打包客户端 (不含 server)"
            echo "  --no-venv         使用系统 python3，不创建 venv"
            echo "  --skip-models     跳过 llama.cpp 二进制下载"
            echo "  --fix-permissions 打包后自动调用 sudo scripts/linux/fix-permissions.sh"
            echo "                    (解决 pynput 读不到 /dev/input/event* 的问题)"
            echo "  --python BIN      指定 Python 解释器路径"
            echo "  --arch ARCH       强制指定目标架构 (aarch64|x86_64)"
            echo "  -h, --help        显示本帮助"
            exit 0
            ;;
        *) log_err "未知参数: $1"; exit 1 ;;
    esac
done

# -----------------------------------------------------------------------------
# 1. 环境检测
# -----------------------------------------------------------------------------
log_info "===== 1. 环境检测 ====="

DETECTED_ARCH="$(uname -m)"
case "$DETECTED_ARCH" in
    aarch64|arm64) ARCH_NAME="aarch64"; MACHINE="aarch64" ;;
    x86_64)        ARCH_NAME="x86_64";  MACHINE="x86_64"  ;;
    *)
        log_err "不支持的架构: $DETECTED_ARCH"
        exit 1
        ;;
esac
if [[ -n "$ARCH_FORCE" ]]; then
    if [[ "$ARCH_FORCE" != "$ARCH_NAME" ]]; then
        log_warn "强制指定架构 $ARCH_FORCE，但当前机器是 $ARCH_NAME"
        log_warn "如使用 QEMU 模拟请确认 binfmt 已配置，否则 PyInstaller 会失败"
    fi
    ARCH_NAME="$ARCH_FORCE"
fi

log_ok "架构: $ARCH_NAME"
log_ok "内核: $(uname -srm)"
log_ok "项目根: $PROJECT_ROOT"

# 平台兼容的 llama.cpp release 资产名
# 注: 工程模板里写的是 Windows 版 llama-b10621-bin-win-vulkan-x64.zip,
#     Linux 上对应的资产名是 ubuntu-vulkan-{arm64,x64}.tar.gz
LLAMACPP_RELEASE_BASE="https://github.com/ggml-org/llama.cpp/releases/download"
LLAMACPP_COMMIT="${LLAMACPP_COMMIT:-b10621}"
case "$ARCH_NAME" in
    aarch64) LLAMACPP_ARCH_TAG="arm64" ;;
    x86_64)  LLAMACPP_ARCH_TAG="x64"   ;;
    *)       log_err "不支持的架构: $ARCH_NAME"; exit 1 ;;
esac
# 优先 vulkan (含 GPU 后端); 若不需要 GPU 可改 LLAMACPP_FLAVOR=cpu
LLAMACPP_FLAVOR="${LLAMACPP_FLAVOR:-vulkan}"
LLAMACPP_FILE="llama-${LLAMACPP_COMMIT}-bin-ubuntu-${LLAMACPP_FLAVOR}-${LLAMACPP_ARCH_TAG}.tar.gz"
LLAMACPP_URL="${LLAMACPP_RELEASE_BASE}/${LLAMACPP_COMMIT}/${LLAMACPP_FILE}"

# -----------------------------------------------------------------------------
# 2. 创建 / 激活 venv
# -----------------------------------------------------------------------------
if [[ "$USE_VENV" == true ]]; then
    log_info "===== 2. 创建 venv ====="
    VENV_DIR="$PROJECT_ROOT/.venv-linux-build"
    if [[ ! -d "$VENV_DIR" ]]; then
        # 选 python 解释器
        if [[ -z "$PYTHON_BIN" ]]; then
            if command -v python3 >/dev/null 2>&1; then
                PYTHON_BIN="$(command -v python3)"
            else
                log_err "找不到 python3"
                exit 1
            fi
        fi
        log_info "使用 Python: $PYTHON_BIN"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
        log_ok "venv 创建完成: $VENV_DIR"
    else
        log_ok "venv 已存在: $VENV_DIR"
    fi
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
    PYTHON="$VENV_DIR/bin/python"
    PIP="$VENV_DIR/bin/pip"
else
    log_warn "===== 2. 跳过 venv，使用系统 Python ====="
    PYTHON="${PYTHON_BIN:-python3}"
    PIP="$PYTHON -m pip"
fi

log_ok "Python: $($PYTHON --version)"
log_ok "pip:    $($PIP --version | head -1)"

# -----------------------------------------------------------------------------
# 3. 安装依赖
# -----------------------------------------------------------------------------
log_info "===== 3. 安装依赖 ====="

# 3.1 系统包提示
log_info "确保以下系统包已安装 (Debian/Ubuntu):"
echo "    sudo apt-get install -y libportaudio2 libportaudiocpp0 libsndfile1 libxcb-cursor0"
echo "    sudo apt-get install -y ffmpeg xclip xsel libnotify-bin"
echo ""

# 3.2 升级 pip
$PIP install --upgrade pip wheel setuptools 2>&1 | tail -3

# 3.3 安装依赖
log_info "安装 pyproject.toml 依赖..."
# 直接基于 pyproject.toml 但替换 onnxruntime-directml 为 onnxruntime
$PIP install \
    "sherpa-onnx" \
    "numpy" \
    "gguf" \
    "onnxruntime" \
    "sentencepiece" \
    "soundfile" \
    "numba" \
    "websockets" \
    "openai" \
    "ollama" \
    "httpx" \
    "rich" \
    "typer" \
    "colorama" \
    "markdown" \
    "markdown-it-py" \
    "tkhtmlview" \
    "pynput" \
    "pyclip" \
    "sounddevice" \
    "watchdog" \
    "pystray" \
    "Pillow" \
    "pypinyin" \
    "srt" \
    "rapidfuzz" \
    "psutil" \
    2>&1 | tail -5

# sounddevice 若 wheel 找不到 libportaudio，需 source 编译
if ! $PYTHON -c "import sounddevice" 2>/dev/null; then
    log_warn "sounddevice wheel 不可用，尝试源码编译 (需 portaudio 开发头文件)"
    log_warn "  安装: sudo apt-get install -y libportaudio19-dev"
    $PIP install --no-binary :all: sounddevice 2>&1 | tail -5 || true
fi

# 3.4 安装打包工具
log_info "安装 PyInstaller..."
$PIP install "pyinstaller>=6.0" "pyinstaller-hooks-contrib" 2>&1 | tail -3

# 3.5 验证关键导入
log_info "验证关键模块..."
$PYTHON -c "
import sys
required = [
    'sherpa_onnx', 'onnxruntime', 'numpy', 'sounddevice',
    'pynput', 'pystray', 'watchdog', 'pypinyin',
    'websockets', 'gguf', 'PIL', 'numba',
    'srt', 'rapidfuzz', 'tkhtmlview',
    'openai', 'ollama', 'httpx', 'typer',
]
failed = []
for m in required:
    try:
        __import__(m)
    except Exception as e:
        failed.append(f'{m}: {e}')
if failed:
    print('FAILED:', failed)
    sys.exit(1)
print('All required modules OK')
"

# -----------------------------------------------------------------------------
# 4. 下载 llama.cpp Linux 二进制 (如果需要)
# -----------------------------------------------------------------------------
LLAMACPP_BIN_DIR="$PROJECT_ROOT/core/server/engines/llama/bin"
mkdir -p "$LLAMACPP_BIN_DIR"

NEED_LLAMACPP=true
if [[ "$CLIENT_ONLY" == true ]]; then
    NEED_LLAMACPP=false
fi
if [[ "$SKIP_MODELS" == true ]]; then
    NEED_LLAMACPP=false
fi

if [[ "$NEED_LLAMACPP" == true ]]; then
    log_info "===== 4. 下载 llama.cpp Linux 二进制 ====="
    if [[ -f "$LLAMACPP_BIN_DIR/libllama.so" ]] && [[ -f "$LLAMACPP_BIN_DIR/libggml.so" ]]; then
        log_ok "llama.cpp 二进制已存在，跳过下载"
    else
        log_info "URL: $LLAMACPP_URL"
        TMP_DIR="$(mktemp -d)"
        TMP_ARCHIVE="$TMP_DIR/llama.tar.gz"
        if command -v curl >/dev/null 2>&1; then
            curl -fL -o "$TMP_ARCHIVE" "$LLAMACPP_URL" || { log_err "下载失败"; rm -rf "$TMP_DIR"; exit 1; }
        elif command -v wget >/dev/null 2>&1; then
            wget -O "$TMP_ARCHIVE" "$LLAMACPP_URL" || { log_err "下载失败"; rm -rf "$TMP_DIR"; exit 1; }
        else
            log_err "需要 curl 或 wget"; exit 1
        fi

        log_info "解压到 $LLAMACPP_BIN_DIR"
        $PYTHON -c "
import tarfile, os, sys
src = '$TMP_ARCHIVE'
dst = '$LLAMACPP_BIN_DIR'
with tarfile.open(src, 'r:gz') as t:
    members = t.getmembers()
    # 找到含 libllama.so 的子目录作为 base
    base = ''
    for m in members:
        if m.name.endswith('libllama.so') and m.isfile():
            base = m.name[:m.name.rindex('libllama.so')]
            break
    print('detected base dir:', repr(base))
    extracted = 0
    for m in members:
        if not m.name.startswith(base):
            continue
        rel = m.name[len(base):]
        if not rel:
            continue
        target = os.path.join(dst, rel)
        if m.isdir():
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with t.extractfile(m) as fin, open(target, 'wb') as fout:
                fout.write(fin.read())
            extracted += 1
    print(f'extracted {extracted} files')
"
        rm -rf "$TMP_DIR"
        # 验证
        if [[ -f "$LLAMACPP_BIN_DIR/libllama.so" ]]; then
            log_ok "libllama.so 已就位"
        else
            log_err "解压后仍找不到 libllama.so，请检查 zip 内容"
            ls -la "$LLAMACPP_BIN_DIR" || true
            exit 1
        fi
    fi
else
    log_warn "===== 4. 跳过 llama.cpp 二进制 ====="
fi

# -----------------------------------------------------------------------------
# 5. 清理与构建
# -----------------------------------------------------------------------------
log_info "===== 5. PyInstaller 构建 ====="
if [[ -d "dist" ]]; then
    log_info "清理旧的 dist/ build/"
    rm -rf dist build
fi

if [[ "$CLIENT_ONLY" == true ]]; then
    SPEC_FILE="build-client-linux.spec"
    OUTPUT_NAME="CapsWriter-Offline-Client"
else
    SPEC_FILE="build_linux.spec"
    OUTPUT_NAME="CapsWriter-Offline"
fi

log_info "使用 spec: $SPEC_FILE"
$PYTHON -m PyInstaller --noconfirm --clean "$SPEC_FILE" 2>&1 | tail -40

if [[ ! -d "dist/$OUTPUT_NAME" ]]; then
    log_err "构建失败: dist/$OUTPUT_NAME 不存在"
    exit 1
fi

log_ok "构建完成: dist/$OUTPUT_NAME/"

# -----------------------------------------------------------------------------
# 6. 验证
# -----------------------------------------------------------------------------
log_info "===== 6. 形态验证 ====="

DIST_DIR="dist/$OUTPUT_NAME"
log_info "可执行文件:"
file "$DIST_DIR/start_server" 2>/dev/null || file "$DIST_DIR/start_client" 2>/dev/null
echo ""
log_info "目录大小:"
du -sh "$DIST_DIR" 2>/dev/null
echo ""
log_info "核心结构:"
ls -la "$DIST_DIR/" 2>&1 | head -25
echo ""
log_info "internal/ 内容 (前 30 项):"
ls "$DIST_DIR/internal" 2>/dev/null | head -30
echo ""
log_info "检测链接:"
find "$DIST_DIR" -maxdepth 2 -type l 2>/dev/null | head -10 || true
echo ""

# smoke test: 启动 1 秒看是否能跑 (仅 import + 解析参数)
log_info "===== 7. Smoke test (无依赖时快速退出) ====="
if [[ -f "$DIST_DIR/start_server" ]]; then
    log_info "测试 start_server --help 或导入..."
    timeout 5s "$DIST_DIR/start_server" --help 2>&1 | head -15 || true
fi

# -----------------------------------------------------------------------------
# 8. 权限状态自检 (Linux 桌面常见坑)
# -----------------------------------------------------------------------------
log_info "===== 8. 权限状态自检 ====="
if [[ -f "$SCRIPT_DIR/fix-permissions.sh" ]]; then
    bash "$SCRIPT_DIR/fix-permissions.sh" --check --user "${SUDO_USER:-$(id -un)}" 2>&1 | tail -25
else
    log_warn "fix-permissions.sh 不存在, 跳过权限检查"
fi

# 8.5 主动修复 (用户传 --fix-permissions 时)
if [[ "$FIX_PERMS" == true ]]; then
    log_info "===== 8.5 调用 fix-permissions.sh 修复 ====="
    if [[ "$EUID" -ne 0 ]]; then
        log_warn "需要 root, 自动用 sudo 重试..."
        sudo bash "$SCRIPT_DIR/fix-permissions.sh" --user "${SUDO_USER:-$(id -un)}" 2>&1
    else
        bash "$SCRIPT_DIR/fix-permissions.sh" --user "$(id -un)" 2>&1
    fi
fi

echo ""
log_ok "============================================================"
log_ok "打包成功！"
log_ok "产物: $DIST_DIR/"
log_ok ""
log_ok "下一步:"
if [[ "$CLIENT_ONLY" == true ]]; then
    log_ok "  1. 将 dist/CapsWriter-Offline-Client/ 复制到目标 Linux 机器"
    log_ok "  2. 安装运行时依赖: sudo apt install libportaudio2 libxcb-cursor0 xclip libnotify-bin"
    log_ok "  3. 修复权限: sudo scripts/linux/fix-permissions.sh  (解决 pynput/麦克风权限)"
    log_ok "  4. ./start_client 即可启动"
else
    log_ok "  1. 将 dist/CapsWriter-Offline/ 复制到目标 Linux 机器"
    log_ok "  2. 安装运行时依赖: sudo apt install libportaudio2 libxcb-cursor0 xclip libnotify-bin ffmpeg"
    log_ok "  3. 修复权限: sudo scripts/linux/fix-permissions.sh  (解决 pynput/麦克风权限)"
    log_ok "  4. 把 Qwen3-ASR 模型放到 dist/CapsWriter-Offline/models/Qwen3-ASR/Qwen3-ASR-1.7B/"
    log_ok "     或运行: ./scripts/linux/download_models.sh --src /path/to/Qwen3-ASR-1.7B-q5_k.zip"
    log_ok "  5. ./start_server 启动服务，再 ./start_client 启动客户端"
fi
log_ok "============================================================"
