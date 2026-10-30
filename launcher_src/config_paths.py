# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 路径与常量（E3 · exe化策划书 v2.4 §五）
铁律：常量化置文件头部，附注释；getattr 防部署漂移。
"""
import os
import re
import sys
from pathlib import Path

# ---------------- 产品常量 ----------------
APP_NAME = "墨忆MOUI-MAX"
APP_ID = "MoUIMax.Launcher"                 # 单实例互斥名（坑⑩）
PRODUCT_NAME = "墨忆MOUI"

# ---------------- 探活账本（坑⑪，E0 实测定稿：3001 已证伪移出） ----------------
PORT_MONGOD = 27017          # mongod TCP LISTENING
PORT_ADAPTER = 8095          # 适配器 LISTENING；NapCat 以客户端 ESTABLISHED 连入
PORT_MAICORE = 8000          # MaiCore maim_message + /dashboard（TCP 可连=她上线）
PORT_ACCOUNT = 8096          # 账号操作服务（随适配器，仅展示用）
DISK_MIN_FREE = 2 * 1024**3  # 五审③：低于 2GB 气泡提醒（mongod 是磁盘满第一个死的）

# ---------------- 进程镜像名 ----------------
IMG_LAUNCHER = "launcher.exe"
IMG_MONGOD = "mongod.exe"
IMG_NAPCAT_BOOT = "NapCatWinBootMain.exe"
IMG_QQ = "QQ.exe"

# 服务名 → 展示名（四盏灯顺序固定：MongoDB/适配器/NapCat/主程序，三审②）
SERVICES = ("mongod", "adapter", "napcat", "bot")
SERVICE_LABELS = {
    "mongod": "MongoDB",
    "adapter": "适配器",
    "napcat": "NapCat",
    "bot": "Moui 主程序",
}

# ---------------- 路径 ----------------
def _install_root() -> Path:
    """launcher.exe 在 {app}\\Launcher\\ → 根=上上级；源码跑在 launcher_src\\ → 根=上级。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


ROOT = _install_root()
IS_FROZEN = bool(getattr(sys, "frozen", False))

MAIBOT_DIR = ROOT / "MaiBot"
ADAPTER_DIR = MAIBOT_DIR / "MaiBot-Napcat-Adapter"
NAPCAT_DIR = MAIBOT_DIR / "napcat"
COMPASS_DIR = MAIBOT_DIR / "MongoDBCompassPortable"
MONGOD_EXE = MAIBOT_DIR / "mongodb" / "bin" / "mongod.exe"
MONGO_DBPATH = MAIBOT_DIR / "mongodb" / "db"
NAPCAT_BOOT_EXE = NAPCAT_DIR / "NapCatWinBootMain.exe"
COMPASS_EXE = COMPASS_DIR / "MongoDBCompass.exe"

# 运行时 python：打包布局 {app}\runtime\python.exe；开发布局 dist\runtime\python.exe
RUNTIME_PYTHON = (ROOT / "runtime" / "python.exe") if IS_FROZEN else (ROOT / "dist" / "runtime" / "python.exe")

# 启动器工作区：MaiBot\logs\launcher\（PID 文件三审④：安装器/停止逻辑靠它精确杀）
LOG_DIR = MAIBOT_DIR / "logs" / "launcher"
PIDS_FILE = LOG_DIR / "pids.json"
LAUNCHER_LOG = LOG_DIR / "launcher.log"
SUB_LOG = {  # 每个子进程一份滚动日志（替代黑框，E3 第 3 条）
    "mongod": "mongod.log",
    "adapter": "adapter.log",
    "napcat": "napcat.log",
    "bot": "bot.log",
}
LOG_ROTATE_BYTES = 5 * 1024 * 1024   # 单文件 5MB 轮转（.log → .log.old）
LOG_UI_TAIL_MAX = 300_000            # UI 日志流缓冲上限（字符）

# 事件流（E3.5 → 神经网窗口数据源）
EVENTS_FILE = MAIBOT_DIR / "logs" / "events.jsonl"

# 版本与更新（E4）
VERSION_FILE = ROOT / "VERSION"
TEMP_UPDATE_DIR = Path(os.environ.get("TEMP", str(ROOT))) / "moui_update"
UPDATE_DONE_FLAG = TEMP_UPDATE_DIR / "update_done.flag"
UPDATE_URL_PRIMARY = "https://moui.mosina.top/version.json"          # 自托管（坑⑬）
UPDATE_URL_FALLBACK = "https://api.github.com/repos/Mosina1102/Moui/releases/latest"
GH_MIRRORS = (                                                       # 镜像链（风险 2）
    "",
    "https://ghproxy.net/",
    "https://mirror.ghproxy.com/",
    "https://gh-proxy.com/",
)
SETUP_NAME_FMT = "MoUIMax_Setup_{ver}_x64.exe"


def read_version() -> str:
    """读产品版本（VERSION 文件=单一事实源；缺失给 0.0.0 兜底）。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return "0.0.0"


def read_maicore_port() -> int:
    """从 MaiBot\\.env 读 PORT（bot.py 经 dotenv 注入；读不到回退 8000）。"""
    env_file = MAIBOT_DIR / ".env"
    try:
        raw = env_file.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "gbk", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        m = re.search(r"^\s*PORT\s*=\s*(\d+)", text, re.M)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return PORT_MAICORE


def config_is_ready() -> bool:
    """首启判定（五审②：向导未完成时跳过更新检查）：bot_config.toml 存在且 qq 键是真实配置的号。
    键名实证：config.py L446 `bot_config.get("qq")`（BOT_QQ 是运行时属性不是 toml 键名）。
    出厂/模板的占位号 1145141919810 算未配置（老大真机实锤：占位号被当已配置 → 全新用户永远弹不出四步引导）。"""
    cfg = MAIBOT_DIR / "config" / "bot_config.toml"
    if not cfg.exists():
        return False
    try:
        raw = cfg.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "gbk", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        m = re.search(r'^\s*qq\s*=\s*["\']?(\d+)', text, re.M | re.IGNORECASE)
        return bool(m and m.group(1) and m.group(1) != "1145141919810")
    except OSError:
        return False
