# -*- coding: utf-8 -*-
"""
Moui 事件流总线（E3.5 · exe化策划书 v2.3 §五·五）
=================================================
「她的大脑」实时神经网动画的唯一数据源：MaiCore 关键路径旁路埋点 → logs/events.jsonl
启动器（E3 第 11 条）tail 该文件渲染节点图。

铁律对齐（Moui施工总方案 〇章 + 策划书 E3.5）：
  · fail-open：emit 全程 try/except 吞异常——埋点写失败绝不影响心流主流程
  · 写入口唯一：全项目只允许 import 本模块的 emit() 写 events.jsonl
  · getattr 防部署漂移：开关读 global_config.event_bus_enable，配置缺失默认开
  · 零决策逻辑：本模块不做任何判断分支，只旁路记录
"""
import json
import os
import time
from datetime import datetime
from pathlib import Path

# ---------------- 常量区（铁律：常量化置文件头） ----------------
EVENTS_DIR = Path("logs")          # 相对 MaiBot cwd（bot 所有日志同目录，启动器按此 tail）
EVENTS_FILE = "events.jsonl"       # 固定文件名（启动器 tail 硬编码此路径）
EVENTS_VERSION = 1                 # 行格式版本，后续加字段向后兼容（E3.5 第 4 条）
RETENTION_DAYS = 7                 # 按天滚动保留 7 天防膨胀（E3.5 第 1 条）
MAX_DETAIL_LEN = 200               # detail 单字段截断，防超长文本撑爆行


class _EventBusState:
    """进程内单例状态（跨模块 import 共享，写入口唯一）。"""

    def __init__(self):
        self._current_day = ""        # "20261030"，跨天时触发滚动

    def _rotate_if_needed(self):
        """按天滚动：events.jsonl → events-YYYYMMDD.jsonl，并清理超期文件。"""
        today = time.strftime("%Y%m%d")
        if self._current_day == today:
            return
        self._current_day = today
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        live = EVENTS_DIR / EVENTS_FILE
        if live.exists() and live.stat().st_size > 0:
            archive = EVENTS_DIR / "events-{}.jsonl".format(today)
            try:
                # 同日已有归档则追加，避免覆盖（同秒跨天重启的极端场景）
                if archive.exists():
                    with open(live, "r", encoding="utf-8", errors="ignore") as src, open(
                        archive, "a", encoding="utf-8", errors="ignore"
                    ) as dst:
                        dst.write(src.read())
                else:
                    live.rename(archive)
            except OSError:
                pass  # 归档失败不阻塞：继续写 live（fail-open）
        # 清理超期归档（fail-open）
        try:
            import glob

            cutoff = time.time() - RETENTION_DAYS * 86400
            for old in glob.glob(str(EVENTS_DIR / "events-*.jsonl")):
                if os.path.getmtime(old) < cutoff:
                    os.remove(old)
        except OSError:
            pass


_state = _EventBusState()


def emit(node: str, stream_id, detail=None) -> None:
    """
    旁路埋点唯一入口。往 logs/events.jsonl append 一行 JSON。
    任何异常一律吞掉（fail-open 铁律：埋点永不影响心流）。
    开关：global_config.event_bus_enable（getattr 默认开，防部署漂移）。
    """
    try:
        # 延迟导入防循环依赖（config 模块 import 链路复杂，不进顶层）
        from src.config.config import global_config

        if not getattr(global_config, "event_bus_enable", True):
            return

        # detail 清洗：只收 dict，值转字符串并截断（防超长 prompt/记忆原文撑爆行）
        clean = {}
        if isinstance(detail, dict):
            for k, v in detail.items():
                try:
                    s = v if isinstance(v, (int, float, bool)) else str(v)
                    if isinstance(s, str) and len(s) > MAX_DETAIL_LEN:
                        s = s[:MAX_DETAIL_LEN] + "…"
                    clean[str(k)] = s
                except Exception:
                    continue

        row = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "v": EVENTS_VERSION,
            "node": str(node),
            "stream_id": str(stream_id) if stream_id is not None else "",
            "detail": clean,
        }
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"

        _state._rotate_if_needed()
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_DIR / EVENTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # fail-open 铁律：埋点写失败（磁盘满/文件只读/编码等）绝不冒泡
        pass
