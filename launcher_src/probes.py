# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 探活与系统检查（E3 第 7 条探活账本）
"""
import os
import socket
import subprocess
import time

import psutil

from config_paths import (
    IMG_NAPCAT_BOOT,
    IMG_QQ,
    PORT_ADAPTER,
    PORT_MONGOD,
    RUNTIME_PYTHON,
)


def probe_port(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    """TCP 可连判定（探活账本的唯一真相源）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port: int, timeout_s: float, interval: float = 1.0) -> bool:
    """轮询等端口就绪。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if probe_port(port):
            return True
        time.sleep(interval)
    return probe_port(port)


def find_image_pids(image: str):
    """按镜像名找 PID（仅用于 QQ/NapCat/mongod 识别，不用于杀我们自己的 python——三审④）。"""
    out = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == image.lower():
                out.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def user_qq_running() -> bool:
    """四审④：QQ 单实例——启动 NapCat 前检测用户自己的 QQ。"""
    return bool(find_image_pids(IMG_QQ))


def napcat_process_alive() -> bool:
    """NapCat 灯双条件之一：NapCatWinBootMain/QQ 进程存在（v2.4：3001 已证伪）。"""
    if find_image_pids(IMG_NAPCAT_BOOT):
        return True
    # QQ 可能是 boot 起的子进程，进程名 QQ.exe 也算（由启动器判定调用方再叠加连接条件）
    return bool(find_image_pids(IMG_QQ))


def adapter_has_napcat_connection() -> bool:
    """NapCat 灯双条件之二：适配器 8095 上存在 ESTABLISHED 连接（NapCat 连入方向，E0 实测）。"""
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if (
                conn.status == psutil.CONN_ESTABLISHED
                and conn.laddr
                and conn.laddr.port == PORT_ADAPTER
            ):
                return True
    except (psutil.AccessDenied, OSError):
        # 无权限枚举时降级：适配器在听即视为连着（fail-open，宁误绿勿误红）
        return probe_port(PORT_ADAPTER)
    return False


def napcat_ready() -> bool:
    """NapCat 就绪 = 进程存在 且 适配器侧活跃连接（双条件缺一黄灯）。"""
    return napcat_process_alive() and adapter_has_napcat_connection()


def is_our_python(pid: int) -> bool:
    """三审④：判 PID 是否我们的 runtime\\python.exe（防误杀用户自己的 Python）。"""
    try:
        proc = psutil.Process(pid)
        exe = proc.exe() or ""
        return exe.lower() == str(RUNTIME_PYTHON).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def taskkill_tree(pid: int) -> None:
    """按 PID 杀进程树（安装器/停止逻辑兜底）。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def kill_image(image: str) -> None:
    """按镜像名杀（仅限确属我们独有的镜像，如 launcher.exe）。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", image],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def kill_image_under_dir(image: str, dir_path) -> int:
    """路径限定击杀：只杀 exe 位于 dir_path 之下的指定镜像。
    老大真机反馈 2026-10-30：无差别 taskkill QQ.exe 会把用户自己的 QQ 弹掉——
    我们的 QQ 在 napcat 目录下，用户的 QQ 在 Tencent 目录，按路径一刀切开。"""
    count = 0
    prefix = str(dir_path).lower()
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info["name"] or "").lower()
            exe = (proc.info["exe"] or "").lower()
            if name == image.lower() and exe.startswith(prefix):
                proc.kill()
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return count


def disk_free_bytes(path) -> int:
    """磁盘余量（五审③）。"""
    try:
        return psutil.disk_usage(str(path)).free
    except OSError:
        return 1 << 62   # 探测失败视为充足（fail-open，不误报）


def terminate_graceful(pid: int, timeout_s: float = 8.0) -> bool:
    """温和终止：terminate → 等退出 → 仍活着才 taskkill 树。"""
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, OSError):
        return True
    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
            return True
        except psutil.TimeoutExpired:
            pass
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        pass
    taskkill_tree(pid)
    try:
        psutil.Process(pid).wait(timeout=5)
        return True
    except (psutil.NoSuchProcess, psutil.TimeoutExpired, OSError):
        return False


def create_no_window_flags() -> int:
    """零黑框核心（E3 第 3 条）。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def create_console_flags() -> int:
    """首启向导/首次扫码：临时控制台窗（坑⑥：唯一允许的黑框例外）。"""
    return getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def env_for_children() -> dict:
    """子进程环境：给 PATH 前置 runtime 目录（NapCat/QQ 需要的 DLL 各自目录已有）。"""
    env = dict(os.environ)
    rt = str(RUNTIME_PYTHON.parent)
    env["PATH"] = rt + os.pathsep + env.get("PATH", "")
    return env
