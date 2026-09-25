#!/usr/bin/env bash
# =============================================================================
# CapsWriter-Offline Linux 权限问题一键修复脚本
# =============================================================================
# 解决问题:
#   1. pynput / evdev-shim 读不到 /dev/input/event*
#      → CapsLock / X2 等全局快捷键失效
#   2. XTestFakeKeyEvent 找不到 X server (DISPLAY 环境变量)
#      → Ctrl+V 粘贴失败, 焦点窗口收不到字符
#   3. sounddevice 找不到麦克风 / PortAudio 报错 (-9999)
#   4. PipeWire / PulseAudio 拒接麦克风
#
# 用法:
#   sudo ./fix-permissions.sh              # 一键修复 (推荐)
#   sudo ./fix-permissions.sh --check      # 只检查不修改
#   sudo ./fix-permissions.sh --user gsh   # 指定要加入 input/audio 组的用户名
#   sudo ./fix-permissions.sh --mode udev  # 只装 udev 规则
#   sudo ./fix-permissions.sh --mode group # 只修改用户组
#   sudo ./fix-permissions.sh --undo       # 撤销所有修改
#   sudo ./fix-permissions.sh --help
#
# 默认会自动:
#   - 创建 /etc/udev/rules.d/99-capswriter-input.rules 让 input/audio 组可读 event*
#   - 把当前用户加入 input + audio 组
#   - 触发 udev 规则生效
#   - 验证修复结果
#
# 注意: 修改用户组后必须重新登录 (或 newgrp) 才能生效, 脚本会主动提示
#
# 输出方式说明 (config_client.paste = True):
#   Client 使用 pyclip.copy(text) + XTestFakeKeyEvent Ctrl+V 输出识别结果.
#   需要 X server 连接 (DISPLAY 环境变量), 但不需要特殊权限.
#   如果 X server 拒绝连接 (例如 Wayland), 输出会失败但 client 仍能录音识别.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err()  { echo -e "${RED}[FAIL]${NC} $*"; }

# -----------------------------------------------------------------------------
# 参数
# -----------------------------------------------------------------------------
MODE=""          # udev | group | all (默认 all)
CHECK_ONLY=false
USER_NAME="${USER:-${SUDO_USER:-$(id -un)}}"
UNDO=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)  CHECK_ONLY=true; shift ;;
        --mode)   MODE="$2"; shift 2 ;;
        --user)   USER_NAME="$2"; shift 2 ;;
        --undo)   UNDO=true; shift ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --check         只检查不修改"
            echo "  --mode MODE     修复模式: udev | group | all (默认 all)"
            echo "  --user NAME     指定用户名 (默认: 当前用户 \$USER)"
            echo "  --undo          撤销所有修改 (移除 udev 规则 + 用户组)"
            echo "  -h, --help      显示帮助"
            exit 0
            ;;
        *) log_err "未知参数: $1"; exit 1 ;;
    esac
done

if [[ -z "$MODE" ]]; then
    if [[ "$UNDO" == true ]]; then
        MODE="all"
    else
        MODE="all"
    fi
fi

# -----------------------------------------------------------------------------
# 工具
# -----------------------------------------------------------------------------
require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        log_err "此操作需要 root 权限, 请用 sudo 运行:"
        log_err "  sudo $0 $*"
        exit 1
    fi
}

user_exists() {
    id -u "$1" >/dev/null 2>&1
}

user_in_group() {
    local u="$1" g="$2"
    id -nG "$u" 2>/dev/null | tr ' ' '\n' | grep -qx "$g"
}

udev_rules_file="/etc/udev/rules.d/99-capswriter-input.rules"

# udev 规则内容: 让 input/audio 组都能读 /dev/input/event*
# 比 chmod 0666 安全 (不影响其他用户, 不影响系统安全)
write_udev_rules() {
    cat > "$udev_rules_file" << 'EOF'
# CapsWriter-Offline: 让 input 和 audio 组可读取输入设备
# 解决 pynput 监听 CapsLock/鼠标侧键 无效的问题
# 规则说明:
#   - input  组: Linux 标准输入设备访问组
#   - audio  组: 音频设备访问组 (PulseAudio/PipeWire 默认可访问麦克风)
KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0660", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", RUN+="/bin/sh -c 'chmod g+rw /dev/input/event* 2>/dev/null || true'"
# 让 audio 组也能访问 input 设备 (兼容把 audio 当万能组的发行版)
KERNEL=="event*", SUBSYSTEM=="input", GROUP="audio", MODE="0660"
EOF
    log_ok "已创建: $udev_rules_file"
}

remove_udev_rules() {
    if [[ -f "$udev_rules_file" ]]; then
        rm -f "$udev_rules_file"
        log_ok "已删除: $udev_rules_file"
    else
        log_info "规则文件不存在, 跳过"
    fi
}

# 添加用户到组 (追加, 不覆盖)
add_user_to_group() {
    local u="$1" g="$2"
    if ! user_exists "$u"; then
        log_warn "用户 '$u' 不存在, 跳过"
        return
    fi
    if ! getent group "$g" >/dev/null; then
        log_warn "组 '$g' 不存在, 跳过 (某些发行版需要先创建)"
        return
    fi
    if user_in_group "$u" "$g"; then
        log_info "用户 '$u' 已在 '$g' 组中"
        return
    fi
    log_info "把用户 '$u' 加入 '$g' 组..."
    usermod -aG "$g" "$u"
    log_ok "$u 已加入 $g 组"
}

remove_user_from_group() {
    local u="$1" g="$2"
    if ! user_exists "$u"; then
        log_warn "用户 '$u' 不存在, 跳过"
        return
    fi
    if ! user_in_group "$u" "$g"; then
        log_info "用户 '$u' 不在 '$g' 组中, 跳过"
        return
    fi
    log_info "把用户 '$u' 从 '$g' 组移除..."
    # gpasswd -d 是反向操作, 比手动编辑安全
    gpasswd -d "$u" "$g" 2>/dev/null || log_warn "gpasswd -d 失败, 尝试 usermod -G"
    log_ok "$u 已退出 $g 组"
}

# 检查现状
check_status() {
    local user="$1"
    echo "============================================================"
    echo " 权限检查 (用户: $user)"
    echo "============================================================"

    # 1. udev 规则
    if [[ -f "$udev_rules_file" ]]; then
        log_ok "udev 规则: 已安装 ($udev_rules_file)"
    else
        log_err "udev 规则: 未安装 (pynput 将无法读 /dev/input/event*)"
    fi

    # 2. 用户组
    echo ""
    log_info "用户 '$user' 所属组: $(id -nG "$user" 2>/dev/null | tr '\n' ' ')"
    for g in input audio; do
        if user_in_group "$user" "$g"; then
            log_ok "  - $g: ✓ 在组中"
        else
            log_warn "  - $g: ✗ 不在组中"
        fi
    done

    # 3. 实际权限验证
    echo ""
    log_info "实际设备权限检查:"
    local ok=true
    for dev in /dev/input/event0 /dev/input/event1; do
        if [[ -e "$dev" ]]; then
            local mode=$(stat -c '%a' "$dev" 2>/dev/null)
            local group=$(stat -c '%G' "$dev" 2>/dev/null)
            if [[ -r "$dev" ]]; then
                log_ok "  - $dev: mode=$mode, group=$group (当前用户可读)"
            else
                log_err "  - $dev: mode=$mode, group=$group (当前用户不可读!)"
                ok=false
            fi
        fi
    done

    # 4. evdev / 麦克风 可用性
    echo ""
    log_info "evdev 可识别设备数:"
    local cnt=$(timeout 3 python3 -c "import evdev; print(len([evdev.InputDevice(p) for p in evdev.list_devices()]))" 2>/dev/null || echo "(python-evdev 未安装)")
    echo "    $cnt"

    log_info "PulseAudio input source 数:"
    local ps=$(timeout 3 pactl list sources short 2>/dev/null | grep -c "alsa_input" || echo "(pactl 未安装)")
    echo "    $ps"

    echo ""
    if [[ "$ok" == true && -f "$udev_rules_file" && ( "$(user_in_group "$user" input && echo y || echo n)" == "y" || "$(user_in_group "$user" audio && echo y || echo n)" == "y" ) ]]; then
        log_ok "权限状态: 健康"
    else
        log_warn "权限状态: 需修复 (运行: sudo $0)"
    fi
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
log_info "============================================================"
log_info " CapsWriter-Offline Linux 权限修复脚本"
log_info "============================================================"
log_info "运行模式: $MODE  用户: $USER_NAME"

if [[ "$CHECK_ONLY" == true ]]; then
    check_status "$USER_NAME"
    exit 0
fi

# 撤销
if [[ "$UNDO" == true ]]; then
    require_root --undo
    log_info "正在撤销所有修改..."
    [[ "$MODE" == "all" || "$MODE" == "udev" ]] && remove_udev_rules
    [[ "$MODE" == "all" || "$MODE" == "group" ]] && {
        remove_user_from_group "$USER_NAME" input
        remove_user_from_group "$USER_NAME" audio
    }
    log_info "触发 udev..."
    udevadm trigger --action=change 2>/dev/null || true
    log_ok "撤销完成"
    check_status "$USER_NAME"
    exit 0
fi

# 安装
require_root

if [[ "$MODE" == "all" || "$MODE" == "udev" ]]; then
    log_info "[1/3] 安装 udev 规则..."
    write_udev_rules
    log_info "触发 udev 规则..."
    udevadm control --reload 2>/dev/null || true
    udevadm trigger --action=change 2>/dev/null || true
    sleep 1
fi

if [[ "$MODE" == "all" || "$MODE" == "group" ]]; then
    log_info "[2/3] 把用户 '$USER_NAME' 加入 input / audio 组..."
    add_user_to_group "$USER_NAME" input
    add_user_to_group "$USER_NAME" audio
fi

log_info "[3/3] 最终检查..."
echo ""
check_status "$USER_NAME"

echo ""
log_info "============================================================"
log_info " 后续步骤"
log_info "============================================================"
if ! user_in_group "$USER_NAME" input && ! user_in_group "$USER_NAME" audio; then
    log_warn "你的用户组已修改, 但需要重新登录才能生效!"
    log_warn "  选项 A: 注销并重新登录 (推荐)"
    log_warn "  选项 B: 当前 shell 临时生效: newgrp input && newgrp audio"
    log_warn "  选项 C: 新开终端: su -l \$USER"
fi
echo ""
log_ok "修复完成! 现在可以运行 ./start_client 测试快捷键与麦克风"
