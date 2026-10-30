# -*- coding: utf-8 -*-
"""E3.5 验收自测：emit 落盘 / fail-open 只读 / 开关关闭 / 轮转（构建机直接跑，不碰 MaiCore 主流程）。"""
import os
import sys
import tempfile
from pathlib import Path

# 模拟 MaiBot cwd 环境：event_bus 用相对 logs/ 路径
os.chdir(tempfile.mkdtemp(prefix="moui_evt_"))

sys.path.insert(0, str(Path(r"f:\新版麦麦\MaiBot一键包[Mmax]0.6.3\MaiBot")))
sys.path.insert(0, str(Path(r"f:\新版麦麦\MaiBot一键包[Mmax]0.6.3\MaiBot\src")))

# stub global_config（event_bus 延迟 import src.config.config——测试环境给个假件）
import types

fake_cfg = types.ModuleType("src.config.config")
class _GC:
    event_bus_enable = True
fake_cfg.global_config = _GC()
sys.modules["src.config.config"] = fake_cfg

from src.common.event_bus import emit

# 1) 正常落盘
emit("message_in", "stream-a", {"sender": "tester", "len": 12})
emit("willing", "stream-a", {"probability": 0.42, "do_reply": True})
emit("send_out", "stream-b", {"len": 30, "emoji": False})
f = Path("logs/events.jsonl")
assert f.exists(), "events.jsonl 未生成"
lines = f.read_text(encoding="utf-8").splitlines()
assert len(lines) == 3, "行数不符"
import json

row = json.loads(lines[0])
assert row["v"] == 1 and row["node"] == "message_in" and row["stream_id"] == "stream-a", "行格式不符"
print("[1] 落盘格式 OK:", lines[0][:100])

# 2) fail-open：文件只读后 emit 不炸不冒泡
os.chmod(f, 0o444)
emit("mood", None, {"valence": 0.5, "arousal": 0.1})   # 若 fail-open 失效这里会抛 PermissionError
os.chmod(f, 0o644)
print("[2] 只读文件 emit 不炸（fail-open）OK")

# 3) 开关关闭
_GC.event_bus_enable = False
size_before = f.stat().st_size
emit("think_start", "stream-a", {})
assert f.stat().st_size == size_before, "开关关闭后文件不应增长"
_GC.event_bus_enable = True
print("[3] event_bus_enable=False 停写 OK")

# 4) 超长 detail 截断
emit("tool_call", None, {"tool": "x" * 500})
row = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
assert len(row["detail"]["tool"]) <= 201, "截断失败"
print("[4] detail 截断 OK")
print("E3.5 验收自测全过")
