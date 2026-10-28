"""O6 每日自动备份 worker（工程优化卡，独立 daemon 线程）。

每日 02:30（避开凌晨 3 点归档窗口）执行一次，当天错过窗口则启动后补跑（幂等）：
1. mongodump 导出 MongoDB（--gzip，参数对齐官方 Database Tools 惯例）
   - 运行时探测 mongodump：包内 mongodb/bin → PATH；两处都没有则跳过库备份只做目录打包（fail-open）
   - 一键包默认不带 Database Tools：补一份 mongodump.exe 放进 mongodb/bin 即自动升级为全量 BSON 备份
2. data/{self_memory,person_cards,world_cards,episodic_cards} 四目录分别 make_archive 打包
3. data/backups/ 按目录名（YYYY-MM-DD 字典序即时间序）滚动保留最近 BACKUP_KEEP_DAYS 份
首次启动立即跑一次（验证整条链路；当天已有完整备份则幂等跳过）。
全程 try/except，失败只打日志，绝不阻断聊天主链路。

恢复演练（人工，一次）：
  mongorestore --gzip --drop --db <库名> data/backups/<日期>/mongodb/<库名>
  目录包（*_zip.zip）直接解压覆盖 data/ 同名目录。
"""
import os
import re
import shutil
import subprocess
import threading
import time
from typing import List, Optional

from src.common.logger_manager import get_logger

logger = get_logger("backup")

# ===== 参数区 =====
BACKUP_HOUR = 2  # 每日备份时刻：2 点（避开 3 点归档窗口）
BACKUP_MINUTE = 30  # 2:30
BACKUP_KEEP_DAYS = 7  # 滚动保留份数（按目录名排序删旧）
BACKUP_ROOT = os.path.join("data", "backups")
MONGODUMP_TIMEOUT = 1800  # dump 子进程超时秒数（一年半大表留足余量）
# data/ 下要打包的目录（人物卡/自我记忆/世界卡/情节卡——Mongo 之外的五谷杂粮全在这）
ARCHIVE_DIRS = ["self_memory", "person_cards", "world_cards", "episodic_cards"]
_DONE_MARKER = ".done"  # 备份完整落盘后写入的标记文件（幂等判据）

_started = False
_last_backup_date = ""


def _find_mongodump() -> Optional[str]:
    """探测 mongodump：包内 mongodb/bin 优先，其次 PATH。都没有返回 None（跳过库备份）。"""
    local = os.path.join("mongodb", "bin", "mongodump.exe")
    if os.path.isfile(local):
        return local
    return shutil.which("mongodump")


def _run_mongodump(dest_dir: str) -> str:
    """执行 mongodump（gzip）。返回结果注记文本；异常向上抛由 run_backup_once 统一记账。"""
    dump_exe = _find_mongodump()
    if not dump_exe:
        return "mongodump 缺失，跳过库备份（放一份 mongodump.exe 到 mongodb/bin 即自动启用）"

    cmd = [dump_exe, "--gzip"]
    uri = os.getenv("MONGODB_URI", "")
    if uri:
        # 官方 tools 禁止 --uri 与 --db/--host 同用，URI 模式只传 --uri
        cmd += ["--uri", uri]
    else:
        cmd += [
            "--host",
            os.getenv("MONGODB_HOST", "127.0.0.1"),
            "--port",
            os.getenv("MONGODB_PORT", "27017"),
        ]
        user = os.getenv("MONGODB_USERNAME", "")
        pwd = os.getenv("MONGODB_PASSWORD", "")
        if user and pwd:
            cmd += ["--username", user, "--password", pwd]
            if os.getenv("MONGODB_AUTH_SOURCE"):
                cmd += ["--authenticationDatabase", os.getenv("MONGODB_AUTH_SOURCE")]
        cmd += ["--db", os.getenv("DATABASE_NAME", "MegBot")]
    cmd += ["--out", os.path.join(dest_dir, "mongodb")]

    proc = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=MONGODUMP_TIMEOUT
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mongodump rc={proc.returncode}: {(proc.stderr or proc.stdout or '')[-300:]}")
    return "mongodump 完成"


def _archive_dirs(dest_dir: str) -> List[str]:
    """打包 ARCHIVE_DIRS 四目录（缺失的跳过）。返回成功打包的 zip 名列表。"""
    packed: List[str] = []
    for name in ARCHIVE_DIRS:
        if not os.path.isdir(os.path.join("data", name)):
            continue
        base = os.path.join(dest_dir, f"{name}_zip")
        shutil.make_archive(base, "zip", root_dir="data", base_dir=name)
        packed.append(f"{name}_zip.zip")
    return packed


def _prune_old_backups() -> None:
    """滚动保留：BACKUP_ROOT 下日期目录按名排序，超出 BACKUP_KEEP_DAYS 的整目录删除。"""
    if not os.path.isdir(BACKUP_ROOT):
        return
    date_dirs = sorted(d for d in os.listdir(BACKUP_ROOT) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d))
    for old in date_dirs[:-BACKUP_KEEP_DAYS] if len(date_dirs) > BACKUP_KEEP_DAYS else []:
        try:
            shutil.rmtree(os.path.join(BACKUP_ROOT, old))
            logger.info(f"[备份] 滚动清理旧备份: {old}")
        except Exception as e:
            logger.error(f"[备份] 清理旧备份失败 {old}: {e}")


def run_backup_once(reason: str = "定时") -> bool:
    """执行一轮备份（幂等：当天目录已有 .done 标记则跳过）。返回是否完整成功。"""
    global _last_backup_date
    date_str = time.strftime("%Y-%m-%d")
    dest_dir = os.path.join(BACKUP_ROOT, date_str)

    if os.path.isfile(os.path.join(dest_dir, _DONE_MARKER)):
        _last_backup_date = date_str
        logger.debug(f"[备份] 今日备份已存在（{dest_dir}），幂等跳过")
        return True

    ok = True
    notes: List[str] = []
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"[备份] 建目录失败: {e}")
        return False

    try:
        notes.append(_run_mongodump(dest_dir))
    except Exception as e:
        ok = False
        logger.error(f"[备份] mongodump 失败: {e}")

    try:
        packed = _archive_dirs(dest_dir)
        if packed:
            notes.append("打包: " + ", ".join(packed))
    except Exception as e:
        ok = False
        logger.error(f"[备份] 目录打包失败: {e}")

    try:
        _prune_old_backups()
    except Exception as e:
        logger.error(f"[备份] 滚动清理异常: {e}")

    # 无论成败都记"当日已尝试"：失败次日再补，绝不满屏重试（fail-open + 防惊群）
    _last_backup_date = date_str
    try:
        with open(os.path.join(dest_dir, _DONE_MARKER), "w", encoding="utf-8") as f:
            f.write(f"{reason} | ok={ok} | {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception:
        pass

    if ok:
        logger.info(f"[备份] 备份完成({reason}) → {dest_dir}（{'；'.join(notes) or '空'}）")
    else:
        logger.warning(f"[备份] 备份部分失败({reason})，明细见上方错误日志")
    return ok


def _worker_loop() -> None:
    global _last_backup_date
    # 首次启动立即跑一次（验证链路；当天已有完整备份则幂等跳过）
    try:
        run_backup_once("首次启动")
    except Exception as e:
        logger.error(f"[备份] 首次备份异常: {e}")

    while True:
        time.sleep(60)
        try:
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            if today == _last_backup_date:
                continue
            if (now.tm_hour, now.tm_min) >= (BACKUP_HOUR, BACKUP_MINUTE):
                run_backup_once("定时")
        except Exception as e:
            logger.error(f"[备份] 调度循环异常: {e}")


def start_backup_worker() -> None:
    """拉起备份线程（幂等）。由 background_tasks 启动时调用。"""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker_loop, name="backup_worker", daemon=True).start()
    logger.info(f"[备份] 每日自动备份线程已启动（每日 {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d}，滚动保留 {BACKUP_KEEP_DAYS} 份）")
