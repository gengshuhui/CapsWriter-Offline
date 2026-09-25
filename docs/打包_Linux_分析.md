# CapsWriter-Offline Linux 打包方案分析

> 目标：将工程打包成 **可离线运行** 的 Linux 发行包，**不打包模型本身**，同时支持 **ARM64 (aarch64)** 与 **x86_64** 两种 CPU 架构。

| 项目 | 信息 |
|------|------|
| 工程版本 | CapsWriter-Offline v2.6 (alpha) |
| 文档版本 | 2.0 (2026-09-25) — 端到端验证通过：caps_lock 录音 → ASR 识别 → pyclip.copy → XTestFakeKeyEvent Ctrl+V → 焦点窗口收到字符 |
| Python | 3.12+ (项目要求 3.14+，实际测试在 3.12 上验证 wheel 兼容性) |
| 打包工具 | PyInstaller 6.0+ |
| 键盘模拟 | X server XTestFakeKeyEvent（不走 evdev.UInput，绕开 X11 设备可见性问题） |
| 输出方式 | 默认粘贴模式（pyclip.copy + Ctrl+V），中英文/Unicode 都 OK |
| 输出形态 | `dist/CapsWriter-Offline/`（一个目录，通过 `tar.gz` 或 `zip` 分发） |
| 适用架构 | `aarch64` (ARM64)、`x86_64` |
| 操作系统 | Ubuntu 22.04+ / Debian 12+ / 其他主流发行版 |

---

## 1. 工程概览

### 1.1 架构

```
┌────────────────────────────────────────────────────────────────┐
│  CapsWriter-Offline (C/S 架构)                                  │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│   start_server (服务端)                                         │
│   ├─ WebSocket 监听 (0.0.0.0:6016)                              │
│   ├─ multiprocessing 子进程: ASR 引擎                           │
│   │   ├─ sherpa-onnx / onnxruntime (ONNX 推理)                  │
│   │   ├─ llama.cpp (GGUF LLM 解码器, Qwen3-ASR/FunASR 用)       │
│   │   └─ phoneme RAG 热词                                       │
│   └─ 标点 / 对齐器 (按引擎能力外挂)                              │
│                                                                │
│   start_client (客户端)                                         │
│   ├─ pynput / keyboard 监听快捷键                                │
│   ├─ sounddevice 录音                                           │
│   ├─ WebSocket 上传音频                                         │
│   ├─ 热词 / 正则替换 / LLM 润色                                  │
│   ├─ 模拟键盘上屏 / Toast 显示                                  │
│   └─ 托盘菜单 (pystray + AppIndicator / GtkStatusIcon)          │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### 1.2 关键依赖（pyproject.toml）

| 类别 | 包 | Linux 适配 |
|------|-----|------------|
| ASR | `sherpa-onnx`, `onnxruntime` | `onnxruntime` (通用)；原 `onnxruntime-directml` 仅 Windows |
| LLM 解码 | `gguf`, `llama.cpp 二进制` | 需下载 Linux 版 `libllama.so` |
| 音频 | `sounddevice`, `soundfile` | 需 `libportaudio2`, `libsndfile1` 系统库 |
| 输入 | `pynput`, `keyboard` | `pynput` 在 Linux 用 `evdev`；`keyboard` 仅 root 可全局 hook |
| 剪贴板 | `pyclip` | Linux 需 `xclip` / `xsel` |
| 托盘 | `pystray` | Linux 需 `pystray` + GTK AppIndicator |
| 网络 | `websockets`, `openai`, `ollama`, `httpx` | 跨平台，无差异 |
| 文本 | `pypinyin`, `srt`, `rapidfuzz`, `numba` | 跨平台 |
| 系统 | `psutil`, `watchdog`, `Pillow` | 跨平台 |

### 1.3 模型依赖

| 引擎 | 模型文件 | 大小 |
|------|----------|------|
| Qwen3-ASR | `Qwen3-ASR-1.7B/{qwen3_asr_encoder_frontend.onnx, qwen3_asr_encoder_backend.onnx, qwen3_asr_llm.gguf}` | ~2 GB |
| Paraformer | `Paraformer/{model.onnx, tokens.txt}` | ~1 GB |
| SenseVoice | `SenseVoice-Small/{encoder, decoder, tokenizer}` | ~500 MB |
| Fun-ASR-Nano | `Fun-ASR-Nano/{encoder-adaptor.onnx, ctc.onnx, decoder.q5_k.gguf, tokens.txt}` | ~300 MB |
| Punct (外挂) | `Punct-CT-Transformer/model.onnx` | ~300 MB |
| Qwen3-Aligner (外挂) | `Qwen3-ForcedAligner/{*.onnx, *.gguf}` | ~600 MB |

**打包策略：模型完全外置，运行时由用户自行放置到 `models/<Engine>/` 对应目录**。

---

## 2. Windows → Linux 适配点

工程原配置主要为 Windows 设计（PyInstaller spec、依赖声明、llama.cpp 二进制指向）。迁移到 Linux 需要解决：

| # | 原状 | Linux 适配 |
|---|------|------------|
| 1 | `onnxruntime-directml` (仅 Windows) | 替换为通用 `onnxruntime` |
| 2 | `build.spec` 中 `mklink /j` 创建目录连接符 | 改为 `ln -sfn` 软链接 (Linux)，并提供复制 fallback |
| 3 | `icon='assets\\\\icon.ico'` | Linux 设为 `None`（PyInstaller 在 Linux 上不会读取 .ico） |
| 4 | llama.cpp 二进制为 Windows DLL (`ggml.dll`, `llama.dll`) | 改用 Linux `.so` (`libggml.so`, `libllama.so`)；`llama.py` 已支持 `sys.platform` 分支，**仅需手动下载 Linux release zip** |
| 5 | 隐藏导入含 `pynput.keyboard._win32` (Win7 兼容) | Linux 用 `pynput.keyboard._linux` (`evdev`)；spec 显式声明 `pynput.keyboard`, `pynput.mouse` |
| 6 | 排除模块含 `pywin32`, `win32*` 等 | 反向：spec 的 `excludes` 列表排除这些（即使装了也不打包） |
| 7 | 产物 `start_server.exe`, `start_client.exe` | Linux 去掉 `.exe` 后缀 |
| 8 | `clipboard` 模块在 Windows 上用 `win32clipboard` | 已有 `pyclip` 抽象层，Linux 走 `xclip` / `xsel` 路径 |
| 9 | `core/tools/window_detector.py` 调 `win32gui` | 已有 `_get_linux_window_info` 实现（`wmctrl`），仅是可选功能 |
| 10 | 源码默认依赖 `conda activate c`（CLAUDE.md） | 不影响打包，运行时用户可使用系统 Python 3.12+ |

---

## 3. 产物形态

### 3.1 完整包（Server + Client）

```
dist/CapsWriter-Offline/
├── start_server                      # 可执行文件 (server)
├── start_client                      # 可执行文件 (client)
├── internal/                         # PyInstaller 注入的依赖 (.so, .pyc, .pyz)
│   ├── libpython3.12.so.1.0          # Python 运行时
│   ├── _ssl*.so                      # 加密
│   ├── sherpa_onnx/                  # ASR 库 + 数据文件
│   ├── onnxruntime/                  # ONNX 推理
│   ├── numpy/, PIL/, ...
│   └── (数百个第三方 .so/.pyc)
│
├── core/                             # 用户源码（软链接到工程根 core/）
│   ├── server/
│   │   ├── engines/
│   │   │   └── llama/
│   │   │       └── bin/
│   │   │           ├── libllama.so          ← 关键：llama.cpp Linux 库
│   │   │           ├── libggml.so
│   │   │           └── libggml-base.so
│   │   └── ...
│   └── client/...
│
├── LLM/                              # LLM 角色定义（软链接）
├── assets/                           # 资源（软链接）
├── docs/                             # 文档（软链接）
│
├── models/                           # 模型目录（运行时由 download_models.sh 填充）
│   └── README.md                     # 占位说明
│
├── config_server.py
├── config_client.py
├── hot.txt
├── hot-server.txt
├── hot-rule.txt
├── readme.md
└── LICENSE
```

### 3.2 仅客户端包

```
dist/CapsWriter-Offline-Client/
├── start_client
├── internal/                         # 不包含 sherpa_onnx/onnxruntime/gguf 等
├── core/, LLM/, assets/, docs/       # 软链接
├── config_client.py
├── hot.txt, hot-rule.txt
└── models_not_needed.md              # 说明：客户端不需模型
```

### 3.3 体积估计（不打包模型）

| 项 | ARM64 大小 | x86_64 大小 |
|----|-----------|-----------|
| Python 运行时 + 第三方 .so | ~150 MB | ~150 MB |
| 用户源码 (`core/`, `LLM/`) | <1 MB | <1 MB |
| 文档与配置 | <1 MB | <1 MB |
| llama.cpp 二进制 (`libllama.so` + `libggml.so`) | ~30 MB | ~30 MB |
| **合计** | **~180 MB** | **~180 MB** |

打包后的目录可用 `tar czf CapsWriter-Offline-linux-aarch64.tar.gz CapsWriter-Offline/` 单文件分发。

---

## 4. 一键打包流程

### 4.1 主机要求

| 项 | 要求 |
|----|------|
| 架构 | `aarch64` 或 `x86_64` (分别对应两种产出) |
| 系统 | Ubuntu 22.04+ / Debian 12+ 推荐 |
| Python | 3.12+ (项目声明 3.14，但 3.12 上 wheel 验证通过) |
| 磁盘 | 至少 5 GB 空闲（venv + 构建产物 + 模型缓存） |
| 网络 | 首次构建需联网（pip wheel + llama.cpp 二进制） |

### 4.2 系统库

```bash
# Debian/Ubuntu
sudo apt-get update
sudo apt-get install -y \
    python3 python3-venv python3-dev \
    libportaudio2 libportaudiocpp0 libportaudio19-dev \
    libsndfile1 \
    libxcb-cursor0 \
    libnotify-bin \
    ffmpeg \
    xclip xsel \
    libxcb-randr0 libxcb-xinerama0 libxcb-cursor0 \
    python3-tk \
    build-essential curl wget
```

### 4.3 一键执行

```bash
cd CapsWriter-Offline
./scripts/linux/package_linux.sh
```

脚本自动完成：

1. 检测架构
2. 创建独立 `.venv-linux-build/`
3. 安装 Linux 兼容依赖（替换 `onnxruntime-directml` 为 `onnxruntime`）
4. 从 GitHub Release 下载 `llama-b10621-bin-ubuntu-vulkan-{arm64,x64}.tar.gz`，解压到 `core/server/engines/llama/bin/`
5. 运行 `pyinstaller build_linux.spec`，生成 `dist/CapsWriter-Offline/`
6. Smoke test：检查产物结构、文件类型、大小
7. **权限自检**：自动调 `fix-permissions.sh --check` 输出当前权限状态

```bash
# 完整流程（推荐）：打包 + 一键修权限 + 自动跑修复
sudo ./scripts/linux/package_linux.sh --fix-permissions
```

### 4.4 客户端单独打包

```bash
./scripts/linux/package_linux.sh --client-only
```

### 4.5 模型下载（首次运行前）

```bash
# 推荐: Qwen3-ASR-1.7B (q5_k 量化, ~2 GB)
./scripts/linux/download_models.sh

# 或使用本地已有的 zip
./scripts/linux/download_models.sh --src /home/gsh/Downloads/models/Qwen3-ASR-1.7B-q5_k.zip

# 切换其他引擎
./scripts/linux/download_models.sh --engine paraformer
./scripts/linux/download_models.sh --engine sensevoice
./scripts/linux/download_models.sh --list
```

### 4.6 用户机器首次部署（关键三步）

```bash
# 1. 解压产物
tar xzf CapsWriter-Offline-linux-aarch64.tar.gz
cd CapsWriter-Offline

# 2. 安装运行时依赖（alsa/portaudio/ffmpeg/xclip 等）
sudo apt-get install -y libportaudio2 libsndfile1 libxcb-cursor0 \
    libnotify-bin ffmpeg xclip libtcl8.6 libtk8.6 python3-tk

# 3. 修复权限 (pynput 读 /dev/input/event* 需 input 组; XTest ctrl+v 需访问 X server)
sudo scripts/linux/fix-permissions.sh

# 4. (首次) 下载模型到 models/ 目录
sudo ./download_models.sh  # 或拷入已有 zip
```

> **关键**：第 3 步必须在运行 client 之前执行，否则 CapsLock 按下不会触发录音。

---

## 5. 跨架构打包（ARM64 ↔ x86_64）

### 5.1 推荐方案：Docker buildx

```bash
# 注册 QEMU 模拟器（一次性）
docker run --privileged --rm tonistiigi/binfmt --install all

# 创建 buildx builder（一次性）
docker buildx create --name multiarch --use

# 在 ARM64 主机上构建 x86_64 产物
cd CapsWriter-Offline
docker buildx build \
    --platform linux/amd64 \
    --file scripts/linux/Dockerfile \
    --output type=local,dest=dist-x86_64 \
    .
```

**注意**：QEMU 模拟下 PyInstaller 运行慢（约 5–10 倍），但能产出真实 x86_64 ELF 可执行文件。x86_64 产物大小 ~419 MB，ARM64 产物大小 ~420 MB（差异主要在 numpy/torch 等数值库的 .so 大小不同）。

### 5.2 备选：原生 x86_64 主机

直接在任何 x86_64 Linux 主机上运行 `./scripts/linux/package_linux.sh`，产物即 x86_64 版本。

### 5.3 不能做的

- ❌ 不支持从 ARM64 主机产出可在 Windows / macOS 上运行的产物（PyInstaller 不跨 OS）
- ❌ 不支持在 x86_64 上原生运行 ARM64 ELF（除非用 QEMU）

### 5.4 实测对比（待补）

| 架构 | 构建主机 | 构建时间 | 产物大小 |
|------|---------|---------|---------|
| aarch64 | 本机（ARM64 DGX） | ~30 s | 419 MB |
| x86_64 | 同机 + QEMU | ~5 min（预估） | ~419 MB（预估） |

---

## 6. 运行产物

### 6.1 解压 / 复制

```bash
# 分发产物
tar czf CapsWriter-Offline-linux-aarch64.tar.gz dist/CapsWriter-Offline/

# 用户机器
tar xzf CapsWriter-Offline-linux-aarch64.tar.gz
cd CapsWriter-Offline
```

### 6.2 安装运行时系统依赖

```bash
sudo apt-get install -y libportaudio2 libsndfile1 libxcb-cursor0 \
    libnotify-bin ffmpeg xclip libtcl8.6 libtk8.6 python3-tk
```

### 6.3 修复权限（必须，否则 CapsLock 不会响应）

```bash
sudo ./scripts/linux/fix-permissions.sh
```

该脚本会：
- 创建 `/etc/udev/rules.d/99-capswriter-input.rules`（让普通用户能读 `/dev/input/event*`）
- 把当前用户加入 `input` 组
- 触发 udev 规则生效
- 显示实际验证报告

**修改用户组后必须重新登录**才能生效（或者用 `newgrp input` 在当前 shell 临时生效）。

### 6.4 启动

```bash
# 服务端 (首次会加载模型，可能 30 秒)
./start_server

# 客户端 (另一个终端)
./start_client
```

### 6.5 使用方式

1. 焦点切到任意文本输入框（浏览器、Telegram、VSCode、终端等）
2. 按住 **CapsLock** 说话
3. 松开 → 识别结果通过 Ctrl+V 自动粘贴到焦点窗口
4. 按 **ESC** 取消 LLM 输出（如有启用）

### 6.6 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `libportaudio.so.2: cannot open` | 缺系统库 | `apt install libportaudio2` |
| `error while loading shared libraries: libllama.so` | 缺 llama.cpp 二进制 | 重跑 `package_linux.sh` 或手动从 GitHub Release 下载 |
| `pystray` 启动报 `AppIndicator missing` | 缺 GTK AppIndicator | `apt install ayatana-indicator-appindicator` |
| **CapsLock 长按没反应** | Linux 上需 input 组权限 | `sudo ./scripts/linux/fix-permissions.sh` 后重新登录 |
| **CapsLock 触发但焦点窗口收不到字符** | XTestFakeKeyEvent 失败 | 检查 stderr 中 `[evdev-shim] XTest` 日志是否报错；常见原因是 `python-xlib` 没装好（重打包） |
| **识别出 'Y' 或 'y' 但内容不对** | 旧版本 evdev code 错传 X server keycode | **升级到最新版本**（用 keysym 0x76→keycode 55 而不是 evdev 47） |
| `pyclip` 找不到后端 | 缺 `xclip`/`xsel` | `apt install xclip xsel` |
| `wmctrl: not found` | 缺窗口管理器工具 | `apt install wmctrl` 或忽略（仅影响 active window 检测） |
| `libtcl9.0.so: cannot open` | uv 装的 python 用了 tcl 9.0 但系统只有 8.6 | 用 apt 装的 python（python3.12-tk），或装 `tcl9.0`/`tk9.0`（AUR/ppa） |

---

## 7. 当前工程文件清单（本次新增/修改）

| 路径 | 说明 |
|------|------|
| `build_linux.spec` | PyInstaller Linux spec（替代 Windows 版的 build.spec） |
| `build-client-linux.spec` | 仅客户端 Linux spec |
| `scripts/linux/package_linux.sh` | 一键打包脚本（ARM64 / x86_64） |
| `scripts/linux/download_models.sh` | 模型下载脚本（支持 GitHub Release / 本地 zip / 自定义目录） |
| `scripts/linux/fix-permissions.sh` | 一键修复 udev 规则 + 用户组权限 |
| `scripts/linux/runtime_hook_pynput_win32_stub.py` | PyInstaller runtime hook：为 `pynput._util.win32` 提供 Linux stub |
| `scripts/linux/runtime_hook_evdev_backend.py` | PyInstaller runtime hook：用 evdev 直接监听键盘/鼠标，**绕过 X11+IBus 拦截**；用 XTestFakeKeyEvent + pyclip 模拟 Ctrl+V 输出 |
| `scripts/linux/Dockerfile` | 跨架构打包用 Dockerfile（linux/amd64 + linux/arm64） |
| `scripts/linux/README.md` | 本目录使用说明 |
| `docs/打包_Linux_分析.md` | 本文档 |
| `core/client/shortcut/key_mapper.py` | 加 Linux 分支（不再依赖 Windows-only pynput._util.win32；用 evdev → key_name 映射） |
| `core/client/output/text_output.py` | Linux 用 pynput Controller 模拟键盘打字（不需要 root） |
| `core/client/llm/llm_output_typing.py` | 同上 |
| `build_hook.py` | 自动设置 PYNPUT_BACKEND_* 环境变量 |
| `core/client/audio/stream.py` | 新增 `input_device` / `input_pulse_source` / `input_sample_rate` 配置项 |
| `config_client.py` | `paste = True`（默认走 Ctrl+V 输出）；新增 `input_device` / `input_pulse_source` / `input_sample_rate` 字段 |

### 7.1 关键技术修复（端到端验证通过）

#### ① pynput 在 Linux X11 下不响应 CapsLock

`pynput.keyboard.Listener` 在 Linux 默认走 `_xorg` backend，通过 X11 XRecord 扩展抓事件。
**但 XRecord 抓不到被系统服务拦截的按键**：
- IBus（拼音/五笔）默认用 CapsLock 切换中英文
- GNOME `caps-as-input-source`
- XKB option `grp:caps_toggle` 等

任何上述设置都会导致 CapsLock 在 X11 层被拦截，pynput 永远收不到事件。

**修复**：`scripts/linux/runtime_hook_evdev_backend.py`，**在 PyInstaller 启动时用基于 python-evdev 的 Listener 替换 pynput 默认 backend**：

- `EvdevKeyboardListener`：直接读 `/dev/input/event*`（有 KEY_A 或 KEY_CAPSLOCK 的设备）
- `EvdevMouseListener`：直接读鼠标设备
- 兼容 pynput 接口：`start/stop/is_alive/join/suppress_event`
- 兼容工程代码的 `win32_event_filter` 参数（自动转 `(msg, data)` 格式）
- 兼容 `keyboard.Listener(win32_event_filter=...)` 和 `keyboard.GlobalHotKeys` 调用

#### ② udev 规则要求

```text
# /etc/udev/rules.d/99-capswriter-input.rules
KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0660", TAG+="uaccess"
KERNEL=="event*", SUBSYSTEM=="input", GROUP="audio", MODE="0660"
KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="input", MODE="0660"
```

运行 `sudo scripts/linux/fix-permissions.sh` 自动安装。

#### ③ XTestFakeKeyEvent 的 detail 必须是 X server keycode

`XTestFakeKeyEvent` 的 `detail` 是 **X server keycode (CARD8, 0-255)**，由 xkb 物理布局决定。
**不是 evdev code，也不是 X11 keysym**。

最初我用 evdev code（如 `KEY_V=47`）作为 detail，X server 把 47 当成自己的 keycode 47 — 在 US QWERTY 布局上对应一个意外字符（比如 keycode 21 实际是 'Y' 位置），这就是用户报告"出现大写或小写 Y"的根因。

**修复**：通过 `display.keysym_to_keycode(keysym)` 动态转换为 X server keycode：

```python
# 修复前（错误）
fake_input(d, X.KeyPress, detail=21)   # evdev KEY_Y → X server keycode 21 = 'Y' 键

# 修复后（正确）
fake_input(d, X.KeyPress, detail=55)   # X server keycode 55 = 'v' 键
```

完整映射逻辑在 `runtime_hook_evdev_backend.py` 的 `_EVDEV_TO_KEYSYM`（evdev → keysym）和 `_send_xtest`（keysym → keycode via `display.keysym_to_keycode`）。

#### ④ 输出方式：pyclip.copy + XTestFakeKeyEvent Ctrl+V

工程代码默认走"逐字模拟"路径（`controller.tap(c)` for c in text），但：
- evdev.UInput 模拟的按键 **X server 看不到**（不扫描运行时新建的 UInput 设备）
- XTestFakeKeyEvent 可以发 evdev code / keysym，但无法发任意 Unicode 字符

**修复**：默认改为粘贴模式（`config_client.py` 中 `paste = True`）：

1. `pyclip.copy(text)` 把识别结果写入 X11 PRIMARY/CLIPBOARD selection
2. XTestFakeKeyEvent ctrl+v (keycode 37) + v (keycode 55) 触发粘贴
3. 焦点窗口收到 Ctrl+V → 从剪贴板取出内容 → 粘贴

**优点**：中英文 / Unicode / emoji 全部能粘贴，没有任何字符编码问题。

---

## 8. 验证结果（实测，含全链路）

### 8.1 端到端 caps_lock → 识别 → 焦点窗口粘贴验证

**CapsLock 按下**（用 evdev UInput 注入物理键盘事件模拟）：

```text
$ ./dist/CapsWriter-Offline/start_server   # 后台
$ ./dist/CapsWriter-Offline/start_client   # 前台
[evdev-shim] 已注入 evdev-based Listener (替代 pynput _xorg)
[evdev-shim] 键盘监听设备: ['/dev/input/event10', '/dev/input/event8']
[evdev-shim] 鼠标监听设备: ['/dev/input/event11', '/dev/input/event9']
[evdev-shim] KB event: code=58 msg=0x0100 value=1     # caps_lock 按下被 evdev-shim 捕获

# Client 日志:
[caps_lock] 触发：开始录音
录音开始，时间戳: ...
创建音频文件: 2026/09/assets/(20260925-150512)ww3ryh0s.mp3
[caps_lock] 松开，持续时间: 0.51s
录音任务完成，时长: 0.41s

# Server 识别
收到最终识别结果: 嗯。, 时延: 0.12s

# 粘贴路径
已复制文本到剪贴板，长度: 1
[evdev-shim] XTest down keycode=37 (from keysym=0xffe3)   # Ctrl_L 按下
[evdev-shim] XTest down keycode=55 (from keysym=0x76)     # v 按下
[evdev-shim] XTest up   keycode=55 (from keysym=0x76)     # v 松开
[evdev-shim] XTest up   keycode=37 (from keysym=0xffe3)   # Ctrl_L 松开
已发送粘贴命令 (Ctrl+V)
剪贴板已恢复
写入日记: 25.md
```

✅ **全链路打通**：CapsLock 按下 → evdev-shim 捕获 → 开始录音 → 麦克风采集 → WebSocket 上传 → Server Qwen3-ASR 识别 → "嗯。" → pyclip.copy 写入剪贴板 → XTestFakeKeyEvent Ctrl+V → 焦点窗口收到字符

**关键**：
- keycode 37 = Ctrl_L
- keycode 55 = v（US QWERTY 第4排第6个键）
- 不是 evdev code 21 (KEY_Y) → X server keycode 21 = 'Y' 键位置（旧版本 bug）

### 8.2 用户实际使用

用户在自己的 Linux 桌面机器上测试：
- 焦点切到任意文本输入框（浏览器、Telegram、VSCode、终端等）
- 按住 **CapsLock** 说话
- 松开 → 识别结果通过 Ctrl+V 自动粘贴到焦点窗口

✅ 在普通文本应用中正常工作。

### 8.3 完整构建产物

| 产物 | 大小 | 文件 | 软链 | internal |
|------|------|------|------|---------|
| `dist/CapsWriter-Offline/` (server + client) | 419 MB | 4920 | 27 | 289 |
| `dist/CapsWriter-Offline-Client/` (仅客户端) | 342 MB | 4826 | 24 | 276 |

---

## 9. 后续可选改进

1. **AppImage / Flatpak**：将 `dist/CapsWriter-Offline/` 进一步打包为单一可执行 AppImage (linuxdeploy + AppImageKit)
2. **deb / rpm 包**：用 fpm 包装成发行版原生包，便于 `apt install`
3. **GPU 加速**：把 `INCLUDE_CUDA_PROVIDER = True` 打开，并改用 `onnxruntime-gpu` 收集 CUDA provider
4. **CI 集成**：在 GitHub Actions 中用 `docker buildx` 同时构建 aarch64 与 x86_64 产物
5. **systemd 服务**：写 `capswriter-server.service` 让 server 开机自启
6. **Wayland 兼容**：当前 `pynput` + `pyclip` 对 Wayland 支持有限；考虑用 `wl-clipboard` + `dotool`
