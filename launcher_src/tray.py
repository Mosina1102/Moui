# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 托盘常驻（webview-first 版）
菜单：显示主窗(面板)/一键启停/单独启动▶(×4)/立即备份/Compass/重新配置/退出
气泡：她上线/更新可用/进程崩溃/磁盘预警。
（K面板独立窗口已废——内置面板全接真后没有两套面板的必要，老大拍板 2026-10-30）
"""
import threading

import pystray
from PIL import IcoImagePlugin  # noqa: F401  冻结态 PIL 插件懒加载缺它 pystray 气泡 save(ICO) 直接炸
from PIL import Image, ImageDraw

from config_paths import APP_NAME, SERVICE_LABELS, SERVICES

_FALLBACK_SIZE = 64


def _make_icon_image():
    """托盘图标：优先 {app}\\Launcher\\moui_logo.ico → 自画枫叶兜底。"""
    from pathlib import Path
    from config_paths import ROOT

    ico = ROOT / "Launcher" / "moui_logo.ico"
    if ico.exists():
        try:
            return Image.open(ico)
        except Exception:
            pass
    img = Image.new("RGBA", (_FALLBACK_SIZE, _FALLBACK_SIZE), (23, 23, 29, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(32, 6), (46, 20), (58, 40), (40, 56), (24, 56), (6, 40), (18, 20)],
              fill=(255, 107, 61, 255))
    d.line([(32, 12), (32, 52)], fill=(120, 40, 20, 255), width=3)
    return img


class Tray:
    def __init__(self, pm, run_wizard, win_holder):
        self.pm = pm
        self.win_holder = win_holder
        self.api = None          # main() 里接线（LauncherApi）
        self._icon = None
        menu = pystray.Menu(
            pystray.MenuItem("显示面板", self._show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("一键启动", lambda: pm.start_all()),
            pystray.MenuItem("一键停止", lambda: pm.stop_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("单独启动", pystray.Menu(*[
                pystray.MenuItem(SERVICE_LABELS[s], (lambda s: lambda: pm.start_service(s))(s))
                for s in SERVICES
            ])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即备份数据库", lambda: pm.backup_now()),
            pystray.MenuItem("打开数据库编辑器 (Compass)", lambda: pm.open_compass()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重新配置", lambda: run_wizard()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon(APP_NAME, _make_icon_image(), APP_NAME, menu)

    def run_detached(self):
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self):
        try:
            self._icon.stop()
        except Exception:
            pass

    def notify(self, text):
        """气泡通知（pystray 原生托盘气泡，任意线程可调）。"""
        try:
            if self._icon is not None and self._icon.visible:
                self._icon.notify(text, APP_NAME)
        except Exception:
            pass

    # ---------------- 菜单动作 ----------------

    def _show(self, icon=None, item=None):
        try:
            w = self.win_holder.get("win")
            if w is not None:
                w.show()
        except Exception:
            pass

    def _quit(self, icon=None, item=None):
        if self.api is not None:
            self.api.quit_app()
        else:
            threading.Thread(target=self.pm.stop_all, daemon=True).start()
