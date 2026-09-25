# CapsWriter-Offline Linux 打包工具集

本目录包含 CapsWriter-Offline 工程在 Linux 平台下的完整打包方案。

## 端到端验证状态 ✅

| 链路环节 | 状态 |
|---------|------|
| CapsLock 按下 → 触发录音 | ✅ |
| evdev-shim 捕获按键（绕开 X11+IBus 拦截） | ✅ |
| 录音 → Server 识别（Qwen3-ASR-1.7B-q5_k） | ✅ |
| 识别结果 → pyclip.copy 写入剪贴板 | ✅ |
| XTestFakeKeyEvent Ctrl+V → 焦点窗口收到粘贴 | ✅ |
| 日记归档 / 音频文件保存 | ✅ |
| **在普通文本应用中正常输入** | ✅ |

## 文件清单

| 文件 | 作用 |
|------|------|
| `package_linux.sh` | 一键打包脚本（venv → 依赖 → llama.cpp → PyInstaller） |
| `download_models.sh` | 模型下载（GitHub Release / 本地 zip / 自定义目录） |
| `fix-permissions.sh` | 一键修复 udev 规则 + 用户组（解决 pynput 输入权限） |
| `build_linux.spec` | PyInstaller spec：完整包（Server + Client） |
| `build-client-linux.spec` | PyInstaller spec：仅客户端 |
| `runtime_hook_pynput_win32_stub.py` | PyInstaller runtime hook：`pynput._util.win32` Linux stub |
| `runtime_hook_evdev_backend.py` | PyInstaller runtime hook：evdev 接管 pynput + XTestFakeKeyEvent Ctrl+V 输出 |
| `Dockerfile` | 跨架构打包（`linux/amd64` + `linux/arm64`） |
| `../../docs/打包_Linux_分析.md` | 完整分析文档 |

## 用户机器首次部署（**必须按此顺序**）

```bash
# 1. 解压
tar xzf CapsWriter-Offline-linux-aarch64.tar.gz
cd CapsWriter-Offline

# 2. 安装系统依赖
sudo apt-get install -y libportaudio2 libsndfile1 libxcb-cursor0 \
    libnotify-bin ffmpeg xclip libtcl8.6 libtk8.6 python3-tk

# 3. 修复权限（必做，否则 CapsLock 不会响应）
sudo ./scripts/linux/fix-permissions.sh
# 改完组后重新登录（newgrp input 仅当前 shell 临时生效）

# 4. 准备模型
sudo ./scripts/linux/download_models.sh  # 从 GitHub Release 下载
# 或拷入已有: ./scripts/linux/download_models.sh --src /path/to/Qwen3-ASR-1.7B-q5_k.zip

# 5. 启动
./start_server   # 一个终端
./start_client   # 另一个终端
```

## 用户使用

1. 焦点切到任意文本输入框（浏览器、Telegram、VSCode、终端等）
2. 按住 **CapsLock** 说话
3. 松开 → 识别结果通过 Ctrl+V 自动粘贴到焦点窗口
4. 按 **ESC** 取消 LLM 输出

## 快速开始

### 本机原生构建

```bash
# 完整打包 (Server + Client)
./package_linux.sh

# 打包 + 自动跑权限修复 (推荐)
sudo ./package_linux.sh --fix-permissions

# 仅客户端
./package_linux.sh --client-only

# 模型下载 (默认 Qwen3-ASR-1.7B-q5_k)
./download_models.sh
./download_models.sh --list  # 列出所有可用引擎
```

### 跨架构构建（ARM64 主机产出 x86_64）

```bash
docker buildx create --name multiarch --use

# 构建 x86_64 产物
docker buildx build \
    --platform linux/amd64 \
    -f scripts/linux/Dockerfile \
    --output type=local,dest=dist-x86_64 \
    .

# 构建 ARM64 产物
docker buildx build \
    --platform linux/arm64 \
    -f scripts/linux/Dockerfile \
    --output type=local,dest=dist-aarch64 \
    .
```

## 输出产物

| 路径 | 大小（ARM64 实测） | 内容 |
|------|-------------------|------|
| `dist/CapsWriter-Offline/` | ~420 MB | 完整包（Server + Client + 源码软链） |
| `dist/CapsWriter-Offline-Client/` | ~342 MB | 仅客户端 |
| `dist/CapsWriter-Offline/models/` | 用户填充 | 模型（外置） |

## 关键技术修复

1. **`onnxruntime-directml` → `onnxruntime`**（pyproject.toml 依赖为 Windows-only）
2. **`mklink /j` → `ln -sfn`（绝对路径）**（Windows 目录连接符无 Linux 对应；必须用绝对路径避免 dist/ 内部自循环）
3. **`icon='*.ico'` → `icon=None`**（PyInstaller 在 Linux 上不读 .ico）
4. **`pynput._util.win32` stub**（key_mapper.py 无条件导入 Windows-only 模块；用 runtime hook 提供空实现）
5. **llama.cpp 二进制下载**（工程模板写的是 Windows zip；Linux 对应的是 `ubuntu-vulkan-{arm64,x64}.tar.gz`）
6. **PipeWire/Pulse source 选择**（sounddevice 在 Linux 桌面看不到物理麦，需用 `PULSE_SOURCE` 环境变量或在 `config_client.input_pulse_source` 指定）

### 端到端链路关键修复

7. **pynput 在 X11+IBus 下不响应 CapsLock** → 用 python-evdev 接管（直接读 `/dev/input/event*`）
8. **udev 规则**：让普通用户能读 input 设备、能写 uinput（用于模拟按键）
9. **XTestFakeKeyEvent 的 detail 必须是 X server keycode**（不是 evdev code 也不是 keysym）：
   ```python
   # 修复前（错）：发 evdev KEY_Y=21 → X server 当成 keycode 21 = 'Y' 键位置 → 用户看到 'Y'
   # 修复后（对）：发 keysym 0x76 → keysym_to_keycode() 转 55 = 'v' 键位置
   ```
10. **输出方式：pyclip.copy + XTestFakeKeyEvent Ctrl+V**（默认 paste=True）
    - 任何 Unicode 字符都能正确粘贴（中英文 / emoji / 特殊符号）
    - 完全绕开 X11 输入法拦截（Ctrl+V 不走 ibus 路由）

## 权限问题一键修复

`pynput` 在 Linux 上读 `/dev/input/event*`（键盘/鼠标事件）需要用户属于 `input` 组，
并且设备文件需要 `input` 组可读。PipeWire/PulseAudio 默认允许 `audio` 组访问麦克风。

**自动化方案**：

```bash
# 一次性修复: 自动创建 udev 规则 + 加入用户组
sudo scripts/linux/fix-permissions.sh

# 只检查不修改
sudo scripts/linux/fix-permissions.sh --check

# 打包时自动调用 (加 --fix-permissions 选项)
sudo scripts/linux/package_linux.sh --fix-permissions

# 撤销 (移 udev 规则 + 退出组)
sudo scripts/linux/fix-permissions.sh --undo
```

修复脚本会：
1. 创建 `/etc/udev/rules.d/99-capswriter-input.rules`（让 input/audio 组可读 event*）
2. 把当前用户加入 `input` 和 `audio` 组
3. 触发 `udevadm trigger` 让规则生效
4. 输出实际权限验证报告

修改用户组后**必须重新登录**才能生效，或用 `newgrp input` 在当前 shell 临时生效。

## 不在打包范围内

- 模型文件（用户自行用 `download_models.sh` 下载或拷贝）
- 系统依赖（libportaudio2、tcl/tk、xclip 等；用户机器自行 `apt install`）
- LLM API（Ollama / OpenAI / DeepSeek 等由用户配置 API key）
