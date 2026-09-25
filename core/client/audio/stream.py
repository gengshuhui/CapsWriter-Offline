# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import os
import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms

    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据
        if not self.state.recording:
            return

        import asyncio

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        self.reopen()

    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream

        # 检测音频设备 (支持按配置显式选择)
        try:
            from config_client import ClientConfig as _Config
            spec = getattr(_Config, 'input_device', None)

            # 在 PipeWire/Pulse 接管 ALSA 的 Linux 桌面环境 (如 Ubuntu GNOME),
            # sounddevice 看不到物理设备名, 只能看到 pipewire/pulse/default 三个虚拟桥。
            # 对此, 我们额外支持: 通过 PulseAudio source 名选择物理麦。
            pulse_source = getattr(_Config, 'input_pulse_source', None)
            if pulse_source:
                os.environ.setdefault('PULSE_SOURCE', pulse_source)
                logger.info(f"已设置环境变量 PULSE_SOURCE={pulse_source}")

            chosen = self._resolve_input_device(spec, pulse_source)
            device = chosen
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            console.print(
                f'使用音频输入设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}")
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError:
            logger.error("未找到麦克风设备")
            input('按回车键退出')
            sys.exit(1)

        # 创建音频流
        try:
            # 选择采样率: 配置优先 → 设备默认 → 工程默认 48000
            from config_client import ClientConfig as _Cfg
            cfg_rate = getattr(_Cfg, 'input_sample_rate', None)
            dev_default = int(device.get('default_samplerate', self.SAMPLE_RATE))
            sample_rate = cfg_rate or dev_default or self.SAMPLE_RATE
            logger.info(f"采样率: {sample_rate} Hz "
                        f"(配置={cfg_rate}, 设备默认={dev_default}, 工程默认={self.SAMPLE_RATE})")

            stream = sd.InputStream(
                samplerate=sample_rate,
                blocksize=int(self.BLOCK_DURATION * sample_rate),
                device=None,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            self.state.stream = stream
            self._running = True
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream

        except sd.PortAudioError as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if '-9999' in str(e):
                console.print("""
[bold red]检测到麦克风被占用或权限异常（错误码 -9999）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None

    def stop(self) -> None:
        """停止音频流"""
        if not self._running:
            return

        self._running = False  # 标记为停止
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None

    @staticmethod
    def _resolve_input_device(spec, pulse_source: str = None) -> dict:
        """
        根据 spec 选择输入设备

        spec:
            None / ''     → 由 sounddevice/PipeWire 自动选默认输入设备
            int            → 按索引选
            str (子串)     → 按设备名做大小写不敏感子串匹配 (首选 in_channels>0 的)
            str (完整名)   → 完全匹配 (含 hostapi 路径时也接受)

        pulse_source:
            在 PipeWire 接管 ALSA 的 Linux 桌面环境, sounddevice 看不到物理麦名。
            此时会从 `pactl list sources short` 解析所有 Pulse source,
            找到名字最匹配 pulse_source (含子串) 的那个, 然后返回其底层 hostapi 索引。
            同时设置环境变量 PULSE_SOURCE 让 sd.InputStream 真正用上它。
        """
        import sounddevice as sd
        import os

        # 1) Pulse source 名 → 找 hostapi 设备
        if pulse_source:
            for i, d in enumerate(sd.query_devices()):
                if d['max_input_channels'] <= 0:
                    continue
                # PipeWire/Pulse 把虚拟桥接设备注册到 ALSA hostapi,
                # 设备名是 'pipewire' / 'pulse' / 'default'
                name_l = d['name'].lower()
                if name_l not in ('pipewire', 'pulse', 'default'):
                    continue
                # sd 的设备名不会显示 Pulse source 名, 但环境变量 PULSE_SOURCE
                # 会让所有 pulse 桥接设备都从指定 source 取流
                os.environ['PULSE_SOURCE'] = pulse_source
                return d

        # 2) 显式 None/空 → 默认
        if spec is None or spec == '':
            return sd.query_devices(kind='input')

        # 3) 整数索引
        if isinstance(spec, int):
            dev = sd.query_devices(spec)
            if dev['max_input_channels'] <= 0:
                raise RuntimeError(f"设备 [{spec}] {dev['name']} 不支持输入")
            return dev

        # 4) 字符串 → 子串匹配 (忽略大小写)
        if isinstance(spec, str):
            needle = spec.lower().strip()
            candidates = []
            for i, d in enumerate(sd.query_devices()):
                if d['max_input_channels'] <= 0:
                    continue
                name = d['name'].lower()
                if needle == name:
                    return d  # 完全匹配优先
                if needle in name:
                    candidates.append((i, d))

            if not candidates:
                # 列出可用输入设备, 帮用户排错
                inputs = [(i, d['name']) for i, d in enumerate(sd.query_devices())
                          if d['max_input_channels'] > 0]
                avail = '\n  '.join(f'[{i}] {n}' for i, n in inputs)
                # 附加 Pulse source 列表 (如果有)
                pulse_hint = ''
                try:
                    out = os.popen("pactl list sources short 2>/dev/null | awk '{print $2}'").read()
                    if out.strip():
                        pulse_hint = '\n\n可用的 Pulse source:\n  ' + '\n  '.join(
                            s for s in out.strip().split('\n') if s
                        )
                except Exception:
                    pass
                raise RuntimeError(
                    f"未找到名称含 '{spec}' 的输入设备。\n"
                    f"可用 sounddevice 设备:\n  {avail}"
                    f"{pulse_hint}\n\n"
                    f"提示: 在 PipeWire 接管的环境下，请改用 config_client.input_pulse_source\n"
                    f"      设置 Pulse source 名 (如 'alsa_input.usb-GN_Audio...')"
                )
            if len(candidates) > 1:
                names = '\n  '.join(f'[{i}] {d["name"]}' for i, d in candidates)
                logger.warning(f"'{spec}' 匹配到多个设备，使用第一个:\n  {names}")
            return candidates[0][1]

        raise TypeError(f"input_device 必须是 None / int / str, 当前: {type(spec).__name__}")

    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流

        Returns:
            新创建的音频输入流
        """
        logger.info("正在重启音频流...")

        # 停止旧流
        self.stop()

        # 重载 PortAudio，更新设备列表
        try:
            sd._terminate()
            sd._ffi.dlclose(sd._lib)
            sd._lib = sd._ffi.dlopen(sd._libname)
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")

        # 等待设备稳定
        time.sleep(0.1)

        # 启动新流
        return self.start()
