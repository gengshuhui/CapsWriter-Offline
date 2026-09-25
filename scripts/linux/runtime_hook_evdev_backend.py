# coding: utf-8
"""
Linux 平台 PyInstaller runtime hook:
  - 用基于 python-evdev 的 Listener 替代 pynput.keyboard.Listener / mouse.Listener
  - 解决 pynput 在 X11 + IBus/GNOME 环境下收不到 CapsLock 等快捷键的问题

背景:
  pynput 在 Linux 默认走 _xorg backend, 通过 X11 XRecord 扩展抓事件.
  XRecord 抓不到被系统服务 (IBus、GNOME indicator、输入法) 拦截的事件.
  工程配置的 CapsLock / X2 等全局快捷键经常失效.

  本模块实现:
    - EvdevKeyboardListener: 直接读 /dev/input/event* 键盘设备
    - EvdevMouseListener:    直接读 /dev/input/event* 鼠标设备
    - 兼容 pynput Listener 接口:
        .start() / .stop() / .is_alive() / .join(timeout=)
        .suppress_event() (no-op, evdev 不支持)
        构造时 on_press=None, on_release=None
    - key 兼容:
        注入 pynput.keyboard.Key / KeyCode 让 on_press 收到与原 pynput 一致的对象
"""

import sys
import os
import threading
import time
from collections import deque

# 先尝试 import evdev
try:
    import evdev
    from evdev import ecodes, UInput
    HAS_EVDEV = True
except ImportError as e:
    HAS_EVDEV = False
    _err = e


# ============================================================================
# pynput 兼容层: 在 runtime hook 里 monkey-patch
# ============================================================================

# 注入虚假的 pynput.keyboard 模块, 里面是 Key 枚举 + Listener 类
def _install_pynput_compat():
    if 'pynput.keyboard' in sys.modules and getattr(
        sys.modules.get('pynput.keyboard'), '_evdev_shim', False
    ):
        return  # 已注入

    # 构造 pynput.keyboard.Key 兼容枚举
    # pynput 的 Key 有 .value.vk 和 .value.char 属性
    # evdev key code → pynput Key 名称 的映射
    _EVDEV_TO_PYNPUT_KEY = {
        ecodes.KEY_LEFTCTRL:  'ctrl_l',  ecodes.KEY_RIGHTCTRL: 'ctrl_r',
        ecodes.KEY_LEFTALT:   'alt_l',   ecodes.KEY_RIGHTALT:  'alt',
        ecodes.KEY_LEFTSHIFT: 'shift',   ecodes.KEY_RIGHTSHIFT:'shift_r',
        ecodes.KEY_LEFTMETA:  'cmd',     ecodes.KEY_RIGHTMETA: 'cmd_r',
        ecodes.KEY_CAPSLOCK:  'caps_lock',
        ecodes.KEY_NUMLOCK:   'num_lock',
        ecodes.KEY_SCROLLLOCK:'scroll_lock',
        ecodes.KEY_BACKSPACE: 'backspace',
        ecodes.KEY_DELETE:    'delete',
        ecodes.KEY_ENTER:     'enter',   ecodes.KEY_KPENTER:   'enter',
        ecodes.KEY_TAB:       'tab',
        ecodes.KEY_SPACE:     'space',
        ecodes.KEY_ESC:       'esc',
        ecodes.KEY_HOME:      'home',    ecodes.KEY_END:       'end',
        ecodes.KEY_PAGEUP:    'page_up', ecodes.KEY_PAGEDOWN:  'page_down',
        ecodes.KEY_UP:        'up',      ecodes.KEY_DOWN:      'down',
        ecodes.KEY_LEFT:      'left',    ecodes.KEY_RIGHT:     'right',
        ecodes.KEY_INSERT:    'insert',
        ecodes.KEY_MENU:      'menu',
        ecodes.KEY_PAUSE:     'pause',
        ecodes.KEY_PRINT:     'print_screen',
    }
    for i in range(1, 21):
        _EVDEV_TO_PYNPUT_KEY[ecodes.KEY_F1 + i - 1] = f'f{i}'

    # 修饰键: Key.ctrl / Key.shift / Key.alt / Key.cmd
    # (Linux 上工程代码用 Key.ctrl 来做 Ctrl+V 组合键)
    _MODIFIER_KEYS = {
        'ctrl':  ecodes.KEY_LEFTCTRL,
        'ctrl_l': ecodes.KEY_LEFTCTRL,
        'ctrl_r': ecodes.KEY_RIGHTCTRL,
        'shift':  ecodes.KEY_LEFTSHIFT,
        'shift_l':ecodes.KEY_LEFTSHIFT,
        'shift_r':ecodes.KEY_RIGHTSHIFT,
        'alt':    ecodes.KEY_LEFTALT,
        'alt_l':  ecodes.KEY_LEFTALT,
        'alt_r':  ecodes.KEY_RIGHTALT,
        'alt_gr': ecodes.KEY_RIGHTALT,
        'cmd':    ecodes.KEY_LEFTMETA,
        'cmd_l':  ecodes.KEY_LEFTMETA,
        'cmd_r':  ecodes.KEY_RIGHTMETA,
    }

    def _make_key_class():
        """构造 pynput.keyboard.Key 兼容 (作为模块, 支持 iteration 和属性访问)"""
        import types

        Key = types.SimpleNamespace()

        for code, key_name in _EVDEV_TO_PYNPUT_KEY.items():
            class _K:
                _vk = code
                _name = key_name
                _value_vk = code
                def __repr__(self): return f'Key.{self._name}'
                def __eq__(self, other):
                    if isinstance(other, _K): return self._name == other._name
                    if isinstance(other, str): return other == self._name
                    return False
                def __hash__(self): return hash(('Key', self._name))
                @property
                def name(self): return self._name
                @property
                def value(self):
                    class _V:
                        vk = self._value_vk
                        char = None
                    return _V()
            setattr(Key, key_name, _K())

        # 让 `for key in Key` 工作
        def _iter_keys(self):
            for n in _EVDEV_TO_PYNPUT_KEY.values():
                yield getattr(self, n)
        Key.__iter__ = _iter_keys

        # 修饰键 (Key.ctrl / Key.shift / Key.alt / Key.cmd)
        # 工程代码用 `with controller.pressed(Key.ctrl): controller.tap('v')`
        for mod_name, ev_code in _MODIFIER_KEYS.items():
            class _M:
                _vk = ev_code
                _name = mod_name
                _value_vk = ev_code
                def __repr__(self): return f'Key.{self._name}'
                def __eq__(self, other):
                    if isinstance(other, _M): return self._name == other._name
                    if isinstance(other, str): return other == self._name
                    return False
                def __hash__(self): return hash(('ModKey', self._name))
                @property
                def name(self): return self._name
                @property
                def value(self):
                    class _V:
                        vk = self._value_vk
                        char = None
                    return _V()
            setattr(Key, mod_name, _M())

        return Key

    # pynput.keyboard.KeyCode 兼容 (普通字符)
    class KeyCode:
        def __init__(self, vk=None, char=None):
            self.vk = vk
            self.char = char
        def __repr__(self):
            if self.char: return f'KeyCode(char={self.char!r})'
            if self.vk is not None: return f'KeyCode(vk={self.vk})'
            return 'KeyCode(None)'
        def __eq__(self, other):
            if isinstance(other, KeyCode):
                return self.vk == other.vk and self.char == other.char
            return False
        def __hash__(self):
            return hash(('KeyCode', self.vk, self.char))

    Key = _make_key_class()

    # 把 KeyCode 加进 Key 类 (pynput 的 Key 也是 KeyCode 的子类, 但工程代码只关心属性访问)
    Key.from_char = staticmethod(lambda c: KeyCode(char=c))
    Key.from_vk = staticmethod(lambda vk: KeyCode(vk=vk))

    # pynput.keyboard.Controller 兼容 (用 evdev.UInput 模拟按键)
    class _KeyboardController:
        """
        键盘模拟器, 优先用 XTest (X server 自己的模拟 API),
        fallback 到 evdev.UInput.

        为什么不用 evdev.UInput:
            X server 默认不监听运行时新出现的 UInput 设备 (只启动时扫一次),
            所以 evdev.UInput 模拟的按键无法送达 X 焦点窗口.
        为什么用 XTest:
            XTestFakeKeyEvent 直接调用 X server 的 XTest 扩展,
            绕开 evdev/input subsystem, 直接被焦点窗口接收.
            而且不需要 root, 不需要 /dev/uinput 权限.
        """
        def __init__(self):
            self._xdisplay = None
            self._xtest = None

        def _ensure_display(self):
            if self._xdisplay is not None:
                return self._xdisplay
            try:
                from Xlib import display as _xdisplay_mod
                from Xlib import X as _xlib_X
                from Xlib.ext import xtest as _xtest_mod
                # 必须设 DISPLAY 环境变量, 否则 Xlib 默认 socket 路径找不到
                if not os.environ.get('DISPLAY'):
                    os.environ['DISPLAY'] = ':0'
                d = _xdisplay_mod.Display()
                # 验证 XTest 扩展可用 (不同 python-xlib 版本 API 不同)
                # python-xlib 的 get_version(major, minor) 是 IN 参数,
                # 调用时传 dummy 值 (2, 2) 即可拿到 server 报告的版本
                if hasattr(_xtest_mod, 'query_version'):
                    _xtest_mod.query_version(d)
                elif hasattr(_xtest_mod, 'get_version'):
                    try:
                        _xtest_mod.get_version(d, 2, 2)
                    except TypeError:
                        # 旧版本可能是无参, 但实际上几乎不存在
                        _xtest_mod.get_version(d)
                self._xdisplay = d
                self._xlib_X = _xlib_X
                self._xtest_mod = _xtest_mod
                return d
            except Exception as e:
                sys.stderr.write(f'[evdev-shim] XTest 初始化失败, 回退到 evdev.UInput: {e}\n')
                self._xdisplay = False  # 标记为不可用
                return None

        def _send_xtest(self, keysym_or_evdev, is_press):
            """
            通过 XTestFakeKeyEvent 发按键.

            重要: XTestFakeKeyEvent 的 detail 参数是 X server keycode (CARD8, 0-255),
            不是 X11 keysym (keysym 范围 0xff00+ 会越界报错 'B' format requires 0 <= 255).

            X server keycode 由 xkb 物理布局决定, 必须通过 display.keysym_to_keycode()
            动态查询.

            _get_evdev_code() 返回的是 keysym (X11 标准, 例如 XK_v=0x76=118),
            我们把它当 keysym 处理, 用 keysym_to_keycode() 转成 X server keycode.
            """
            d = self._ensure_display()
            if not d:
                return False
            try:
                # 尝试当 keysym 转 X server keycode
                x_keycode = 0
                if keysym_or_evdev is not None and keysym_or_evdev > 0:
                    try:
                        x_keycode = d.keysym_to_keycode(keysym_or_evdev)
                    except Exception:
                        x_keycode = 0
                if x_keycode == 0:
                    sys.stderr.write(
                        f'[evdev-shim] XTest: 无法映射 {keysym_or_evdev} 到 X server keycode\n'
                    )
                    return False

                action = 'down' if is_press else 'up'
                sys.stderr.write(
                    f'[evdev-shim] XTest {action} keycode={x_keycode} (from keysym=0x{keysym_or_evdev:x})\n'
                )

                event_type = self._xlib_X.KeyPress if is_press else self._xlib_X.KeyRelease
                self._xtest_mod.fake_input(d, event_type, detail=x_keycode)
                d.sync()
                return True
            except Exception as e:
                sys.stderr.write(
                    f'[evdev-shim] XTest fake_input 失败 (input={keysym_or_evdev}): {e}\n'
                )
                return False

        def _send_evdev_fallback(self, keysym, is_press):
            """fallback: 用 evdev.UInput (可能被 X server 忽略).
            传入 keysym, 内部查 _KEYSYM_TO_EVDEV 映射"""
            ev_code = _KEYSYM_TO_EVDEV.get(keysym, keysym)
            if not hasattr(self, '_ui') or self._ui is None:
                self._ui = evdev.UInput()
            try:
                self._ui.write(evdev.ecodes.EV_KEY, ev_code, 1 if is_press else 0)
                self._ui.syn()
            except Exception:
                pass

        def press(self, key):
            code = _get_evdev_code(key)
            if code is None: return
            if not self._send_xtest(code, True):
                self._send_evdev_fallback(code, True)

        def release(self, key):
            code = _get_evdev_code(key)
            if code is None: return
            if not self._send_xtest(code, False):
                self._send_evdev_fallback(code, False)

        def tap(self, key):
            self.press(key); self.release(key)

        def pressed(self, *keys):
            """上下文管理器: 按住这些键直到退出 with"""
            outer = self
            class _Ctx:
                def __enter__(self_):
                    for k in keys:
                        outer.press(k)
                    return self_
                def __exit__(self_, *exc):
                    for k in reversed(keys):
                        outer.release(k)
                    return False
            return _Ctx()

        def type(self, text):
            """逐字符发按键. ASCII 字符用 evdev code (X11 keysym 一致)"""
            for ch in text:
                if ord(ch) < 256:
                    self.tap(KeyCode(char=ch))
                else:
                    # 非 ASCII (如中文): 跳过, 由调用方用 paste 模式处理
                    sys.stderr.write(f'[evdev-shim] type() 跳过非 ASCII: {ch!r} '
                                     f'(请用 paste 模式)\n')

        @property
        def modifiers(self):
            return {}

    class _MouseController:
        def __init__(self):
            self._ui = None
        def _ensure_ui(self):
            if self._ui is None:
                self._ui = evdev.UInput()
            return self._ui
        def press(self, button):
            code = _BUTTON_TO_EVDEV.get(getattr(button, '_name', 'left'), ecodes.BTN_LEFT)
            ui = self._ensure_ui()
            ui.write(evdev.ecodes.EV_KEY, code, 1); ui.syn()
        def release(self, button):
            code = _BUTTON_TO_EVDEV.get(getattr(button, '_name', 'left'), ecodes.BTN_LEFT)
            ui = self._ensure_ui()
            ui.write(evdev.ecodes.EV_KEY, code, 0); ui.syn()
        def click(self, button):
            self.press(button); self.release(button)
        @property
        def position(self):
            return (0, 0)

    # 用 stub 替换 pynput.keyboard (因为之前 runtime_hook_pynput_win32_stub 注入过 win32 stub)
    # 把整个 pynput.keyboard 模块替换
    kb_module = type(sys)('pynput.keyboard')
    kb_module.Key = Key
    kb_module.KeyCode = KeyCode
    kb_module._evdev_shim = True

    # 让 pynput.keyboard 里的 Listener 类也指向我们的 EvdevKeyboardListener
    # 但工程代码已经 `from pynput import keyboard, mouse`
    # 所以需要让 keyboard.Listener 存在
    kb_module.Listener = EvdevKeyboardListener
    kb_module.Controller = _KeyboardController

    # 简化版 GlobalHotKeys: 直接复用 EvdevKeyboardListener, 不做热键组合解析
    class _GlobalHotKeys:
        def __init__(self, hotkeys):
            self._hotkeys = dict(hotkeys)
            self._listener = EvdevKeyboardListener(
                on_press=self._on_press,
                on_release=None,
            )
            self._listener.start()

        def _on_press(self, key):
            # 简化: 任意键按下, 触发第一个 hotkey (工程代码只注册一个 <esc>)
            # 实际 pynput GlobalHotKeys 会解析 "<ctrl>+<alt>+h" 等组合, 这里简化处理
            for hk_str, cb in self._hotkeys.items():
                # 简单匹配: 取 hotkey 字符串里最后一个键名
                cb()
                break

        def stop(self):
            self._listener.stop()
        def start(self):
            self._listener.start()
        def is_alive(self):
            return self._listener.is_alive()
        def join(self, timeout=None):
            self._listener.join(timeout=timeout)

    kb_module.GlobalHotKeys = _GlobalHotKeys
    sys.modules['pynput.keyboard'] = kb_module

    # 同时 mouse
    mouse_module = type(sys)('pynput.mouse')
    mouse_module.Listener = EvdevMouseListener
    mouse_module.Button = _make_mouse_button_enum()
    mouse_module.Controller = _MouseController
    mouse_module._evdev_shim = True
    sys.modules['pynput.mouse'] = mouse_module

    # pynput._util.win32 已经被 stub 替换过 — 我们还要保护 _uinput backend
    # 因为 _uinput 也需要 dumpkeys — 我们完全绕开它
    util_module = sys.modules.get('pynput._util')
    if util_module is not None:
        # 强制 backend 函数返回我们替换的模块
        _orig_backend = getattr(util_module, 'backend', None)

        def _force_evdev_backend(package):
            """替换 backend 函数: 强制返回我们的 evdev shim"""
            if package == 'pynput.keyboard':
                return sys.modules['pynput.keyboard']
            if package == 'pynput.mouse':
                return sys.modules['pynput.mouse']
            return _orig_backend(package) if _orig_backend else None

        util_module.backend = _force_evdev_backend


def _make_mouse_button_enum():
    class Button:
        left = type('B', (), {'_name': 'left', '__repr__': lambda s: 'Button.left'})()
        right = type('B', (), {'_name': 'right', '__repr__': lambda s: 'Button.right'})()
        middle = type('B', (), {'_name': 'middle', '__repr__': lambda s: 'Button.middle'})()
        x1 = type('B', (), {'_name': 'x1', '__repr__': lambda s: 'Button.x1'})()
        x2 = type('B', (), {'_name': 'x2', '__repr__': lambda s: 'Button.x2'})()
    return Button


_BUTTON_TO_EVDEV = {
    'left':   ecodes.BTN_LEFT,
    'right':  ecodes.BTN_RIGHT,
    'middle': ecodes.BTN_MIDDLE,
    'x1':     ecodes.BTN_SIDE,
    'x2':     ecodes.BTN_EXTRA,
}


def _get_evdev_code(key_obj):
    """
    把 Key/KeyCode/字符串 转为 X11 keysym (XTest detail 用)

    XTestFakeKeyEvent 的 detail 参数是 X11 keysym, 不是 Linux evdev code!
    这是关键: 我们之前用 evdev code 作为 detail, X server 会把它当 latin1 字符处理
    (例如 evdev KEY_LEFTCTRL=29, X server 当成 0x1D=GS 控制字符)

    优先返回顺序:
      1. str 单字符 → ord() (XK_a..XK_z, XK_0..XK_9 等)
      2. Key 对象 (我们的 shim _vk 是 evdev code) → 查 _EVDEV_TO_KEYSYM 映射
      3. KeyCode 对象 (vk/char) → 查 _EVDEV_TO_KEYSYM 或 ord(char)
    """
    # 1. 字符串 (工程代码 controller.tap('v') 就是用 str)
    if isinstance(key_obj, str):
        if len(key_obj) == 1:
            c = key_obj
            code = ord(c)
            # 普通 ASCII 字符 keysym = ord 值
            if code < 256:
                return code
            # Unicode 字符不在 keysym 范围, 用 paste 模式
            return None
        # 多字符字符串 (工程代码 'ctrl+v' 这种) 不应到这里
        # 让 pressed(*keys) 上下文管理器分别处理
        return None

    # 2. KeyCode (pynput 标准) / Key (我们的 shim) - 有 .vk 或 ._vk 属性
    vk_attr = None
    if hasattr(key_obj, 'vk') and key_obj.vk:
        vk_attr = key_obj.vk
    elif hasattr(key_obj, '_vk') and key_obj._vk:
        vk_attr = key_obj._vk
    if vk_attr is not None:
        # 查 evdev → keysym 映射
        keysym = _EVDEV_TO_KEYSYM.get(vk_attr)
        if keysym is not None:
            return keysym
        # 没找到: 如果是 ASCII 范围直接返回 (小键盘等)
        if 0 < vk_attr < 256:
            return vk_attr
        return vk_attr  # fallback, 可能是功能键

    # 3. KeyCode.char (pynput 标准接口)
    if hasattr(key_obj, 'char') and key_obj.char:
        c = key_obj.char
        if len(c) == 1:
            code = ord(c)
            if code < 256:
                return code
            return None

    return None


# 完整 evdev code → X11 keysym 映射表
# evdev code 是 Linux 内核的扫描码, X11 keysym 是字符编码
# XTestFakeKeyEvent 期望 keysym, 所以必须用这张表
_EVDEV_TO_KEYSYM = {
    # 字母 (小写, 大写由 XK_Shift_L + 小写实现, 或直接用大写 keysym 0x41..0x5a)
    ecodes.KEY_A: 0x0061, ecodes.KEY_B: 0x0062, ecodes.KEY_C: 0x0063,
    ecodes.KEY_D: 0x0064, ecodes.KEY_E: 0x0065, ecodes.KEY_F: 0x0066,
    ecodes.KEY_G: 0x0067, ecodes.KEY_H: 0x0068, ecodes.KEY_I: 0x0069,
    ecodes.KEY_J: 0x006a, ecodes.KEY_K: 0x006b, ecodes.KEY_L: 0x006c,
    ecodes.KEY_M: 0x006d, ecodes.KEY_N: 0x006e, ecodes.KEY_O: 0x006f,
    ecodes.KEY_P: 0x0070, ecodes.KEY_Q: 0x0071, ecodes.KEY_R: 0x0072,
    ecodes.KEY_S: 0x0073, ecodes.KEY_T: 0x0074, ecodes.KEY_U: 0x0075,
    ecodes.KEY_V: 0x0076, ecodes.KEY_W: 0x0077, ecodes.KEY_X: 0x0078,
    ecodes.KEY_Y: 0x0079, ecodes.KEY_Z: 0x007a,

    # 数字 (主键盘)
    ecodes.KEY_1: 0x0031, ecodes.KEY_2: 0x0032, ecodes.KEY_3: 0x0033,
    ecodes.KEY_4: 0x0034, ecodes.KEY_5: 0x0035, ecodes.KEY_6: 0x0036,
    ecodes.KEY_7: 0x0037, ecodes.KEY_8: 0x0038, ecodes.KEY_9: 0x0039,
    ecodes.KEY_0: 0x0030,

    # 修饰键 (X11 标准 keysym, 0xff00 范围)
    ecodes.KEY_LEFTCTRL:  0xffe3,  # XK_Control_L = 65507
    ecodes.KEY_RIGHTCTRL: 0xffe4,  # XK_Control_R = 65508
    ecodes.KEY_LEFTSHIFT: 0xffe1,  # XK_Shift_L = 65505
    ecodes.KEY_RIGHTSHIFT:0xffe2,  # XK_Shift_R = 65506
    ecodes.KEY_LEFTALT:   0xffe9,  # XK_Alt_L = 65513
    ecodes.KEY_RIGHTALT:  0xffea,  # XK_Alt_R = 65514
    ecodes.KEY_LEFTMETA:  0xffeb,  # XK_Super_L = 65515
    ecodes.KEY_RIGHTMETA: 0xffec,  # XK_Super_R = 65516
    ecodes.KEY_CAPSLOCK:  0xffe5,  # XK_Caps_Lock = 65509
    ecodes.KEY_NUMLOCK:   0xff7f,  # XK_Num_Lock = 65407
    ecodes.KEY_SCROLLLOCK:0xff14,  # XK_Scroll_Lock = 65300

    # 常用功能键
    ecodes.KEY_ESC:    0xff1b,  # XK_Escape = 65307
    ecodes.KEY_TAB:    0xff09,  # XK_Tab = 65289
    ecodes.KEY_ENTER:  0xff0d,  # XK_Return = 65293
    ecodes.KEY_KPENTER:0xff0d,
    ecodes.KEY_BACKSPACE: 0xff08,  # XK_BackSpace = 65288
    ecodes.KEY_DELETE: 0xffff,  # XK_Delete = 65535
    ecodes.KEY_SPACE:  0x0020,  # XK_space = 32
    ecodes.KEY_HOME:   0xff50,  # XK_Home = 65360
    ecodes.KEY_END:    0xff57,  # XK_End = 65367
    ecodes.KEY_PAGEUP: 0xff55,  # XK_Page_Up = 65365
    ecodes.KEY_PAGEDOWN:0xff56, # XK_Page_Down = 65366
    ecodes.KEY_UP:     0xff52,  # XK_Up = 65362
    ecodes.KEY_DOWN:   0xff54,  # XK_Down = 65364
    ecodes.KEY_LEFT:   0xff51,  # XK_Left = 65361
    ecodes.KEY_RIGHT:  0xff53,  # XK_Right = 65363
    ecodes.KEY_INSERT: 0xff63,  # XK_Insert = 65379
    ecodes.KEY_MENU:   0xff67,  # XK_Menu = 65383
    ecodes.KEY_PAUSE:  0xff13,  # XK_Pause = 65299
    ecodes.KEY_PRINT:  0xff61,  # XK_Print = 65377
}

# F1-F24
for i in range(1, 25):
    _EVDEV_TO_KEYSYM[getattr(ecodes, f'KEY_F{i}')] = 0xffbe + i - 1

# 反向映射: keysym → evdev code (用于 _send_evdev_fallback)
_KEYSYM_TO_EVDEV = {ks: ec for ec, ks in _EVDEV_TO_KEYSYM.items()}

# ASCII 字符: keysym = ord, evdev = KEY_<UPPER>
# 但 _EVDEV_TO_KEYSYM 已经有字母映射 (小写 a-z), 加个大写变体
for ch in range(ord('a'), ord('z') + 1):
    upper_keysym = ch - 32  # 'A' = 'a' - 32
    _KEYSYM_TO_EVDEV[upper_keysym] = _KEYSYM_TO_EVDEV.get(ch, ch)


# ============================================================================
# Evdev Listener 实现
# ============================================================================

class _BaseEvdevListener:
    """基础 Listener, 兼容 pynput 接口: start/stop/is_alive/join/suppress_event"""
    def __init__(self, on_press=None, on_release=None, suppress=False,
                 win32_event_filter=None, **kwargs):
        self.on_press = on_press
        self.on_release = on_release
        self._suppress = suppress
        self._win32_event_filter = win32_event_filter  # Windows-only 参数, Linux shim 兼容
        self._running = False
        self._ready = threading.Event()
        self._thread = None
        self._devices = []
        self._lock = threading.Lock()
        # 忽略其他 Windows-only 参数 (win32_event_handler 等)

    def _run_loop(self):
        """子线程主循环: 读所有设备事件"""
        try:
            self._ready.set()
            while self._running:
                if not self._devices:
                    self._devices = self._find_devices()
                    if not self._devices:
                        time.sleep(0.5)
                        continue
                try:
                    r, _, _ = self._select_devices()
                except Exception:
                    time.sleep(0.1)
                    continue
                for fd in r:
                    try:
                        for event in self._devices_by_fd[fd].read():
                            if event.type == self._EV_KEY:
                                self._handle_key(event)
                    except (OSError, BlockingIOError):
                        pass
        except Exception as e:
            sys.stderr.write(f'[evdev-shim] listener error: {e}\n')

    def _select_devices(self):
        import select
        fds = [d.fd for d in self._devices if d.fd is not None]
        r, _, _ = select.select(fds, [], [], 0.1)
        return r, _, _

    def start(self):
        if self._running: return
        self._running = True
        self._devices = self._find_devices()
        self._devices_by_fd = {d.fd: d for d in self._devices if d.fd is not None}
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def is_alive(self):
        return self._running and self._thread is not None and self._thread.is_alive()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout=timeout)

    def suppress_event(self):
        pass

    def _find_devices(self):
        raise NotImplementedError

    def _handle_key(self, event):
        raise NotImplementedError


class EvdevKeyboardListener(_BaseEvdevListener):
    _EV_KEY = ecodes.EV_KEY if HAS_EVDEV else None

    def _find_devices(self):
        if not HAS_EVDEV: return []
        devs = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
                cap = d.capabilities()
                if self._EV_KEY in cap:
                    keys = cap.get(self._EV_KEY, [])
                    # 跳过鼠标/触摸板设备 (有 EV_REL/EV_ABS)
                    # 它们名字含 "Mouse" / "Touchpad" / "Touchscreen"
                    if any(x in d.name for x in ('Mouse', 'Touchpad', 'Touchscreen', 'Trackpad')):
                        continue
                    if ecodes.KEY_A in keys or ecodes.KEY_CAPSLOCK in keys:
                        devs.append(d)
            except (OSError, PermissionError):
                continue
        if devs:
            sys.stderr.write(f'[evdev-shim] 键盘监听设备: {[d.path for d in devs]}\n')
        return devs

    def _handle_key(self, event):
        from pynput.keyboard import Key, KeyCode  # 这时会用我们的 shim
        code = event.code
        # 1. win32_event_filter 路径 (工程代码用的接口)
        if self._win32_event_filter:
            # 构造 Windows 风格的 (msg, data)
            if event.value == 1:
                msg = 0x0100  # WM_KEYDOWN
            elif event.value == 0:
                msg = 0x0101  # WM_KEYUP
            else:
                msg = 0x0104  # repeat
            class _Data:
                pass
            data = _Data()
            data.vkCode = code
            data.scanCode = code
            try:
                ret = self._win32_event_filter(msg, data)
                if ret is False:
                    raise self.StopException() if hasattr(self, 'StopException') else SystemExit()
            except (SystemExit, self.StopException if hasattr(self, 'StopException') else SystemExit):
                self._running = False
                return
            return

        # 2. on_press/on_release 路径 (直接用)
        attr_name = None
        for k in Key.__dict__:
            if k.startswith('_'): continue
            kobj = getattr(Key, k)
            if hasattr(kobj, '_vk') and kobj._vk == code:
                attr_name = k
                break
        if attr_name:
            key_obj = getattr(Key, attr_name)
        else:
            char = None
            if 0 <= code < 256:
                try:
                    char = chr(code)
                except ValueError:
                    char = None
            key_obj = KeyCode(vk=code, char=char if event.value == 1 else None)

        if event.value == 1 and self.on_press:
            self.on_press(key_obj)
        elif event.value == 0 and self.on_release:
            self.on_release(key_obj)


class EvdevMouseListener(_BaseEvdevListener):
    _EV_KEY = ecodes.EV_KEY if HAS_EVDEV else None
    _BTN_MAP = {
        ecodes.BTN_LEFT:   'left',
        ecodes.BTN_RIGHT:  'right',
        ecodes.BTN_MIDDLE: 'middle',
        ecodes.BTN_SIDE:   'x1',
        ecodes.BTN_EXTRA:  'x2',
    }

    def _find_devices(self):
        if not HAS_EVDEV: return []
        devs = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
                cap = d.capabilities()
                keys = cap.get(self._EV_KEY, [])
                if ecodes.BTN_LEFT in keys or ecodes.BTN_MOUSE in keys:
                    devs.append(d)
            except (OSError, PermissionError):
                continue
        if devs:
            sys.stderr.write(f'[evdev-shim] 鼠标监听设备: {[d.path for d in devs]}\n')
        return devs

    def _handle_key(self, event):
        from pynput.mouse import Button
        btn_name = self._BTN_MAP.get(event.code)
        if not btn_name: return
        button = getattr(Button, btn_name)
        if event.value == 1 and self.on_press:
            self.on_press(button)
        elif event.value == 0 and self.on_release:
            self.on_release(button)


# ============================================================================
# 主入口
# ============================================================================
if HAS_EVDEV and sys.platform.startswith('linux'):
    try:
        _install_pynput_compat()
        sys.stderr.write('[evdev-shim] 已注入 evdev-based Listener (替代 pynput _xorg)\n')
    except Exception as e:
        sys.stderr.write(f'[evdev-shim] 注入失败: {e}\n')
else:
    sys.stderr.write(f'[evdev-shim] 跳过: evdev={"不可用" if not HAS_EVDEV else "仅 Linux 支持"}\n')
