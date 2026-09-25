# coding: utf-8
"""
按键映射相关

处理按键名称和虚拟键码之间的转换，以及相关常量定义。
"""

import sys

if sys.platform == 'win32':
    from pynput import keyboard
    from pynput._util.win32 import KeyTranslator
    _key_translator = KeyTranslator()
    # 特殊键 VK 映射（从 pynput 复制）
    _SPECIAL_KEYS = {
        key.value.vk: key
        for key in keyboard.Key
    }
else:
    # Linux / macOS: 直接构造 VK 映射表 (pynput 在这些平台上没有 KeyTranslator / Key.value.vk)
    _key_translator = None
    # 让 pynput.keyboard.Key 能被 import (即使用我们的 evdev shim)
    from pynput import keyboard as _kb

    # evdev key code -> 工程用的 key_name 映射 (Linux 上 data.vkCode 就是 evdev code)
    # 工程代码 self.tasks 用 'caps_lock', 'x1', 'x2' 等作 key
    _VK_TO_NAME = {
        # modifiers (evdev KEY_* 常量值与 X11 keysym 略有差异, 选最常见)
        42: 'caps_lock',   # KEY_CAPSLOCK
        58: 'caps_lock',
        29: 'ctrl_l',      # KEY_LEFTCTRL
        97: 'ctrl_r',      # KEY_RIGHTCTRL
        56: 'alt_l',       # KEY_LEFTALT
        100: 'alt_r',      # KEY_RIGHTALT
        42: 'shift_l',     # KEY_LEFTSHIFT
        54: 'shift_r',     # KEY_RIGHTSHIFT
        125: 'cmd_l',      # KEY_LEFTMETA
        126: 'cmd_r',      # KEY_RIGHTMETA
        # 常用键
        14: 'backspace',
        28: 'enter', 96: 'enter',
        15: 'tab',
        57: 'space',
        1:  'esc',
        102: 'home',
        107: 'end',
        103: 'up',
        108: 'down',
        105: 'left',
        106: 'right',
        109: 'page_down',
        104: 'page_up',
        110: 'insert',
        139: 'menu',
        119: 'pause',
        99:  'print_screen',
        69:  'num_lock',
        70:  'scroll_lock',
        # F1-F12
        59: 'f1', 60: 'f2', 61: 'f3', 62: 'f4',
        63: 'f5', 64: 'f6', 65: 'f7', 66: 'f8',
        67: 'f9', 68: 'f10', 87: 'f11', 88: 'f12',
        # F13-F24
        183: 'f13', 184: 'f14', 185: 'f15', 186: 'f16',
        187: 'f17', 188: 'f18', 189: 'f19', 190: 'f20',
        191: 'f21', 192: 'f22', 193: 'f23', 194: 'f24',
    }
    _SPECIAL_KEYS = {}  # 兼容旧代码, Linux 上 VkMapper.vk_to_name 用 _VK_TO_NAME 优先


# 小键盘按键映射（VK -> 名称）
NUMPAD_KEYS = {
    0x60: 'numpad0',  0x61: 'numpad1',  0x62: 'numpad2',  0x63: 'numpad3',
    0x64: 'numpad4',  0x65: 'numpad5',  0x66: 'numpad6',  0x67: 'numpad7',
    0x68: 'numpad8',  0x69: 'numpad9',
    0x6A: 'numpad_multiply',  # *
    0x6B: 'numpad_add',       # +
    0x6C: 'numpad_separator', # （通常未使用）
    0x6D: 'numpad_subtract',  # -
    0x6E: 'numpad_decimal',   # 小数点
    0x6F: 'numpad_divide',    # /
}

# Windows 键盘消息常量
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Windows 鼠标消息常量
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# 按键消息集合
KEYBOARD_MESSAGES = (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
KEY_UP_MESSAGES = (WM_KEYUP, WM_SYSKEYUP)
KEY_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)
MOUSE_MESSAGES = (WM_XBUTTONDOWN, WM_XBUTTONUP)

# 可恢复的切换键（需要录音完成后恢复状态的锁键）
RESTORABLE_KEYS = {
    'caps_lock',    # 大写锁定
    'num_lock',     # 数字键盘锁定
    'scroll_lock',  # 滚动锁定
}


class KeyMapper:
    """按键映射器"""

    # pynput 特殊键对象缓存
    _SPECIAL_KEY_OBJECTS = None

    @classmethod
    def _get_special_key_objects(cls):
        """获取 pynput 特殊键对象（延迟初始化）"""
        if cls._SPECIAL_KEY_OBJECTS is None:
            # 直接从 pynput keyboard.Key 枚举构建 name -> key 映射，
            # 覆盖全部特殊键（含 insert/home/end/方向键/page_up 等），避免手工枚举遗漏
            cls._SPECIAL_KEY_OBJECTS = {
                key.name: key
                for key in keyboard.Key
            }
        return cls._SPECIAL_KEY_OBJECTS

    @staticmethod
    def vk_to_name(vk: int) -> str:
        """
        将虚拟键码转换为按键名称

        Args:
            vk: 虚拟键码

        Returns:
            str: 按键名称（与 Shortcut.key 格式一致）
        """
        # 首先检查是否是特殊键（pynput Key 枚举）
        if vk in _SPECIAL_KEYS:
            return _SPECIAL_KEYS[vk].name

        # 检查是否是小键盘按键
        if vk in NUMPAD_KEYS:
            return NUMPAD_KEYS[vk]

        # Linux: 先用 _VK_TO_NAME 映射 (caps_lock 等)
        if sys.platform != 'win32' and vk in _VK_TO_NAME:
            return _VK_TO_NAME[vk]

        # 使用 pynput 的 KeyTranslator 获取字符（字母、数字、符号键）
        try:
            params = _key_translator(vk, is_press=True)
            if 'char' in params and params['char'] is not None:
                return params['char']
        except Exception:
            pass

        # Linux 普通字符键: 直接当 ASCII 字符 (evdev code < 256 = ASCII 码)
        if sys.platform != 'win32' and 0 <= vk < 256:
            try:
                return chr(vk)
            except ValueError:
                pass

        # 未知键码，返回 vk_ 格式
        return f'vk_{vk}'

    @staticmethod
    def name_to_key(key_name: str):
        """
        将按键名称转换为 pynput 按键对象

        Args:
            key_name: 按键名称

        Returns:
            pynput 按键对象或 None
        """
        # 特殊按键
        special_keys = KeyMapper._get_special_key_objects()
        if key_name in special_keys:
            return special_keys[key_name]

        # 单个字符按键
        if len(key_name) == 1:
            return keyboard.KeyCode.from_char(key_name)

        logger.warning(f"未知按键名称: {key_name}")
        return None
