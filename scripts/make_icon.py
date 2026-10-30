#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Moui_logo.png 生成多尺寸 moui_logo.ico（E3 托盘/安装包/E4 图标三处共用）+ 面板资源暂存。"""
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Moui_logo.png"
OUT_INSTALLER = ROOT / "installer" / "moui_logo.ico"
OUT_LAUNCHER = ROOT / "dist" / "Launcher" / "moui_logo.ico"

# 面板资源暂存（build_launcher.bat 是纯 ASCII，中文文件名由这里处理——坑⑦）
PANEL_STAGE = ROOT / ".build" / "panel"
PANEL_SRC_HTML = ROOT / "Moui面板原型.html"
PANEL_SRC_JS = ROOT / "launcher_src" / "panel_bridge.js"

img = Image.open(SRC).convert("RGBA")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
OUT_INSTALLER.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT_INSTALLER, format="ICO", sizes=sizes)
OUT_LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT_LAUNCHER, format="ICO", sizes=sizes)
print("ICO 生成:", OUT_INSTALLER, "和", OUT_LAUNCHER)

if PANEL_SRC_HTML.exists():
    if PANEL_STAGE.exists():
        shutil.rmtree(PANEL_STAGE)
    PANEL_STAGE.mkdir(parents=True)
    shutil.copy2(PANEL_SRC_HTML, PANEL_STAGE / "panel.html")
    shutil.copy2(SRC, PANEL_STAGE / "Moui_logo.png")
    if not PANEL_SRC_JS.exists():
        raise SystemExit("[ERROR] 桥接脚本缺失: {}".format(PANEL_SRC_JS))
    shutil.copy2(PANEL_SRC_JS, PANEL_STAGE / "panel_bridge.js")
    print("面板资源暂存:", PANEL_STAGE)
else:
    print("警告: 面板原型缺失", PANEL_SRC_HTML)
