import sys
import os
from os.path import dirname, join, exists

# 将「执行文件所在目录」添加到「模块查找路径」
# 这确保了可以找到复制的源文件（config.py, core/ 等）
executable_dir = dirname(sys.executable)
sys.path.insert(0, executable_dir)

# PyInstaller 打包时，第三方依赖（DLL, PYD）放在 internal/ 目录
# 需要将 internal/ 也添加到路径，否则 Python 无法找到这些依赖
internal_dir = join(executable_dir, 'internal')
if exists(internal_dir):
    sys.path.insert(0, internal_dir)

# ----------------------------------------------------------------------------
# Linux 平台: 强制 pynput 用 uinput (evdev) backend
# ----------------------------------------------------------------------------
# 背景:
#   pynput 默认在 Linux 上选 'xorg' backend, 通过 X11 XRecord 扩展捕获按键.
#   但 X11 XRecord 抓不到被 IBus / GNOME indicator / 快捷键服务拦截的事件,
#   比如 CapsLock (用作输入法切换) 经常被抓不到, 导致 CapsLock 快捷键失效.
#   pynput 还提供 '_uinput' backend (基于 evdev), 它直接读 /dev/input/event*,
#   能抓到所有底层键盘事件, 不受 X11 / 输入法拦截.
#
# 用环境变量 PYNPUT_BACKEND_{pkg}=uinput 强制选择 (必须在 import pynput 之前).
# 用户也可以通过 CAPSWRITER_PYNPUT_BACKEND=auto|xorg|uinput 覆盖此默认.
# ----------------------------------------------------------------------------
if sys.platform.startswith('linux'):
    user_choice = os.environ.get('CAPSWRITER_PYNPUT_BACKEND', 'uinput').lower()
    if user_choice in ('uinput', 'auto', 'xorg'):
        os.environ.setdefault('PYNPUT_BACKEND_KEYBOARD', user_choice if user_choice != 'auto' else 'uinput')
        os.environ.setdefault('PYNPUT_BACKEND_MOUSE', user_choice if user_choice != 'auto' else 'uinput')
        # 把 _uinput 路径加进 sys.modules 是必要的 (PyInstaller noarchive 模式)
        # 让 pynput._util.backend 能找到

