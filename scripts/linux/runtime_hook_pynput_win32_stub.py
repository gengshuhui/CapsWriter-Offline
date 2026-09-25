# coding: utf-8
"""
Linux 平台 PyInstaller runtime hook: 为 pynput._util.win32 提供 stub 模块

背景:
  - 原工程 key_mapper.py 无条件 from pynput._util.win32 import KeyTranslator
  - pynput 在 Linux 上没有 _util.win32 子模块
  - Linux 上模拟键盘用 XTest / ydotool，不需要 VK 翻译
  - 我们 stub 一个空实现的 KeyTranslator，让 key_mapper 仍能 import
  - 运行时调用 KeyTranslator(...) 会抛异常，触发原代码的 fallback 路径
    (见 key_mapper.py 第 99-104 行的 try/except)
"""

import sys
import types


def _install_stub():
    if 'pynput._util.win32' in sys.modules:
        return

    mod = types.ModuleType('pynput._util.win32')

    class _StubKeyTranslator:
        """在 Linux 上完全空的 VK->char 翻译器，调用即抛错以触发调用方 fallback"""

        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, vk, is_press=True, modifiers=None):
            raise NotImplementedError(
                'KeyTranslator not available on Linux (use pynput.keyboard directly). '
                'X11/Wayland key simulation does not use VK codes.'
            )

    mod.KeyTranslator = _StubKeyTranslator

    # 一些 Linux 兼容 shim：pynput 内部也会尝试访问这些属性
    class _StubMappedValue:
        vk = None
        char = None

    mod.MappedValue = _StubMappedValue
    mod.VK_CODE = {}

    sys.modules['pynput._util.win32'] = mod


_install_stub()
