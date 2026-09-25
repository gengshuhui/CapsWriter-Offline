# coding: utf-8
"""
单实例检测

防止同一用户在同一台机器上启动多个 client 实例, 导致
caps_lock 被多次触发, 产生重复录音/识别/粘贴.

实现: PID 文件 + fcntl 文件锁
- 启动时尝试获取 /tmp/capswriter_client_<uid>.lock
- 获取成功 → 写入当前 PID, 启动 client
- 获取失败 → 检查锁文件里的 PID 是否还在跑
  - 在跑 → 拒绝启动, 提示用户
  - 已死 (stale) → 删除锁文件, 重新尝试
"""

import os
import sys
import fcntl
import tempfile
from pathlib import Path
from . import logger


LOCK_DIR = Path(tempfile.gettempdir())
LOCK_FILE = LOCK_DIR / f"capswriter_client_{os.getuid()}.lock"


class SingleInstanceError(Exception):
    """另一个 client 实例已经在跑"""

    def __init__(self, existing_pid: int):
        self.existing_pid = existing_pid
        super().__init__(
            f"检测到另一个 CapsWriter Client 实例在运行 (PID {existing_pid}).\n"
            f"一个机器上跑多个 client 没意义, 因为所有实例都会响应 caps_lock, 导致重复录音/识别/粘贴.\n"
            f"请先关闭已有的实例:\n"
            f"  - 在托盘菜单右键点击 '退出'\n"
            f"  - 或运行: kill {existing_pid}\n"
            f"  - 或运行: pkill -f start_client"
        )


def _pid_alive(pid: int) -> bool:
    """检查 PID 是否还在跑"""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        # ProcessLookupError: PID 不存在
        # PermissionError: PID 存在但属于其他用户 (算 alive)
        return True
    except OSError:
        return False


def acquire() -> None:
    """
    尝试获取单实例锁.

    Raises:
        SingleInstanceError: 另一个实例在跑
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)

    # 先检查已有锁文件, 看 PID 是否还活着
    if LOCK_FILE.exists():
        try:
            content = LOCK_FILE.read_text().strip()
            old_pid = int(content)
            if _pid_alive(old_pid):
                raise SingleInstanceError(old_pid)
            else:
                logger.warning(f"移除过期的锁文件 (旧 PID {old_pid} 已死)")
                LOCK_FILE.unlink()
        except (ValueError, FileNotFoundError):
            # 内容无效或被并发删除, 继续
            pass

    # 创建新锁文件 (atomic), 获取排他锁
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        # 另一个进程在我们检查 PID 和尝试 flock 之间抢到了锁
        # 重新读 PID 报错
        os.close(fd)
        try:
            content = LOCK_FILE.read_text().strip()
            old_pid = int(content)
            raise SingleInstanceError(old_pid)
        except (ValueError, FileNotFoundError):
            raise SingleInstanceError(0)

    # 拿到了锁, 写 PID
    os.write(fd, f"{os.getpid()}\n".encode())
    os.fsync(fd)
    # fd 保持打开, 进程退出时自动释放锁
    # (用 atexit 显式释放更干净, 但 OS 进程退出也会释放)


def release() -> None:
    """释放单实例锁 (进程退出时调用)"""
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


import atexit
atexit.register(release)
