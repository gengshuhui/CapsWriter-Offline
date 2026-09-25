#!/usr/bin/env bash
# =============================================================================
# CapsWriter-Offline Linux 模型下载脚本
# =============================================================================
# 作用:
#   自动从 GitHub Release / 用户本地 zip 包 / 已有目录等多种来源
#   补齐打包产物所需的 ASR 模型 (默认 Qwen3-ASR-1.7B-q5_k)
#
# 用法:
#   ./download_models.sh                              # 默认 Qwen3-ASR-1.7B-q5_k
#   ./download_models.sh --engine qwen3               # 指定引擎
#   ./download_models.sh --engine paraformer         # 下载 Paraformer
#   ./download_models.sh --engine sensevoice         # 下载 SenseVoice
#   ./download_models.sh --engine fun-asr-nano       # 下载 Fun-ASR-Nano
#   ./download_models.sh --src /path/to/local.zip    # 从本地 zip 解压
#   ./download_models.sh --list                      # 列出所有可用引擎与说明
#   ./download_models.sh --target-dir /custom/path   # 自定义安装目录
#
# 输出目录 (默认):
#   models/<Engine>/<ModelName>/...
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err()  { echo -e "${RED}[FAIL]${NC} $*"; }

# 默认参数
ENGINE="qwen3"
TARGET_DIR="models"
SRC_ZIP=""
LIST_ONLY=false

# -----------------------------------------------------------------------------
# 引擎配置表 (引擎 -> 模型相对路径, 模型 zip URL)
# -----------------------------------------------------------------------------
# 这些 URL 对应 GitHub Release: https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models
# 若有变动请更新。
declare -A MODEL_URLS=(
    ["qwen3"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Qwen3-ASR-1.7B-q5_k.zip"
    ["paraformer"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Paraformer.zip"
    ["sensevoice"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/SenseVoice-Small.zip"
    ["fun-asr-nano"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Fun-ASR-Nano.zip"
    ["punct"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Punct-CT-Transformer.zip"
    ["qwen3-aligner"]="https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Qwen3-ForcedAligner.zip"
)

declare -A MODEL_DESCRIPTIONS=(
    ["qwen3"]="Qwen3-ASR-1.7B (q5_k 量化) — 当前推荐，最准，自带标点"
    ["paraformer"]="Paraformer-large — 速度最快，CPU 友好"
    ["sensevoice"]="SenseVoice-Small — 多语言 (中日英韩粤)，自带标点"
    ["fun-asr-nano"]="Fun-ASR-Nano-GGUF — 综合性能优秀"
    ["punct"]="CT-Transformer 标点模型 — 当主引擎不自带标点时外挂"
    ["qwen3-aligner"]="Qwen3-ForcedAligner — 强制对齐器，文件转录时间戳"
)

declare -A MODEL_TARGET_PATHS=(
    ["qwen3"]="Qwen3-ASR/Qwen3-ASR-1.7B"
    ["paraformer"]="Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    ["sensevoice"]="SenseVoice-Small/Sensevoice-Small-ONNX"
    ["fun-asr-nano"]="Fun-ASR-Nano/Fun-ASR-Nano-GGUF"
    ["punct"]="Punct-CT-Transformer/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12"
    ["qwen3-aligner"]="Qwen3-ForcedAligner/Qwen3-ForcedAligner-0.6B"
)

# -----------------------------------------------------------------------------
# 参数解析
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)      ENGINE="$2"; shift 2 ;;
        --src)         SRC_ZIP="$2"; shift 2 ;;
        --target-dir)  TARGET_DIR="$2"; shift 2 ;;
        --list)
            LIST_ONLY=true
            shift
            ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --engine ENG        指定引擎: qwen3 (默认) | paraformer | sensevoice | fun-asr-nano | punct | qwen3-aligner"
            echo "  --src ZIP_PATH       使用本地 zip 文件 (跳过下载)"
            echo "  --target-dir DIR     自定义安装根目录 (默认: models/)"
            echo "  --list               列出所有可用引擎"
            echo "  -h, --help           显示帮助"
            exit 0
            ;;
        *) log_err "未知参数: $1"; exit 1 ;;
    esac
done

if [[ "$LIST_ONLY" == true ]]; then
    echo "可用引擎列表:"
    for eng in "${!MODEL_DESCRIPTIONS[@]}"; do
        printf "  %-18s — %s\n" "$eng" "${MODEL_DESCRIPTIONS[$eng]}"
    done
    exit 0
fi

if [[ -z "${MODEL_URLS[$ENGINE]:-}" ]]; then
    log_err "未知引擎: $ENGINE (使用 --list 查看可用列表)"
    exit 1
fi

MODEL_REL_PATH="${MODEL_TARGET_PATHS[$ENGINE]}"
MODEL_FINAL_DIR="$TARGET_DIR/$MODEL_REL_PATH"

log_info "============================================================"
log_info " 引擎:   $ENGINE"
log_info " 描述:   ${MODEL_DESCRIPTIONS[$ENGINE]}"
log_info " 目标:   $MODEL_FINAL_DIR/"
log_info "============================================================"

# 检查是否已存在
if [[ -d "$MODEL_FINAL_DIR" ]] && [[ -n "$(ls -A "$MODEL_FINAL_DIR" 2>/dev/null)" ]]; then
    log_warn "目录已存在且非空: $MODEL_FINAL_DIR"
    read -rp "是否覆盖? [y/N] " ans
    if [[ "${ans:-N}" != "y" && "${ans:-N}" != "Y" ]]; then
        log_info "已取消"
        exit 0
    fi
    rm -rf "$MODEL_FINAL_DIR"
fi

mkdir -p "$TARGET_DIR"

# 解压 zip 到临时目录
TMP_DIR="$(mktemp -d)"
TMP_ZIP="$TMP_DIR/model.zip"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ -n "$SRC_ZIP" ]]; then
    log_info "使用本地 zip: $SRC_ZIP"
    if [[ ! -f "$SRC_ZIP" ]]; then
        log_err "本地 zip 不存在: $SRC_ZIP"
        exit 1
    fi
    cp "$SRC_ZIP" "$TMP_ZIP"
else
    MODEL_URL="${MODEL_URLS[$ENGINE]}"
    log_info "下载: $MODEL_URL"
    if command -v curl >/dev/null 2>&1; then
        if ! curl -fL -o "$TMP_ZIP" "$MODEL_URL"; then
            log_err "下载失败"
            log_warn "你可以手动下载后用 --src 指定本地路径"
            exit 1
        fi
    elif command -v wget >/dev/null 2>&1; then
        if ! wget -O "$TMP_ZIP" "$MODEL_URL"; then
            log_err "下载失败"
            exit 1
        fi
    else
        log_err "需要 curl 或 wget"
        exit 1
    fi
fi

log_info "解压到 $MODEL_FINAL_DIR/ ..."
mkdir -p "$(dirname "$MODEL_FINAL_DIR")"

# 用 python zipfile 处理，避免解压路径不匹配
python3 - <<PYEOF
import zipfile, os, sys
src = "$TMP_ZIP"
final = "$MODEL_FINAL_DIR"
with zipfile.ZipFile(src) as z:
    names = z.namelist()
    # 找到顶层目录 (如 Qwen3-ASR-1.7B/ 或 model/)，统一去掉
    base = ''
    if names:
        first = names[0]
        if first.endswith('/'):
            base = first
    extracted = 0
    for n in names:
        if base and not n.startswith(base):
            continue
        rel = n[len(base):] if base else n
        if not rel:
            continue
        target = os.path.join(final, rel)
        if n.endswith('/'):
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as fin, open(target, 'wb') as fout:
                fout.write(fin.read())
            extracted += 1
    print(f'extracted {extracted} files')
PYEOF

# 验证: 列出结果
log_ok "模型就位！目录内容:"
ls -la "$MODEL_FINAL_DIR" | head -20
echo ""
log_ok "============================================================"
log_ok "完成。下次启动 server 时会自动加载此模型。"
log_ok "如要切换引擎，请修改 config_server.py 中的 model_type"
log_ok "============================================================"
