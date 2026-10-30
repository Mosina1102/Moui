# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 总入口（webview-first 重构 2026-10-30）
==============================================================
架构（老大拍板：面板就是主界面，不要驾驶舱）：
  · 主窗口 = pywebview 全屏加载 墨忆MOUI 面板（原型经桥接脚本注入后活起来）
  · LauncherApi（js_api 桥）：面板 JS ←→ ProcManager（启停/状态/日志/事件/更新/备份）
  · 启动即自动拉起全家桶（config 就绪时）；未就绪 → 面板首启向导 → 完成后自动启动
  · 托盘 pystray 常驻（关窗=缩托盘）；单实例互斥；冷启动接管
  · tkinter/customtkinter 全部移除——"未响应"真凶就是 tkinter 与 WebView2 两套消息循环互掐

启动序：
  1) 单实例互斥（坑⑩） 2) 日志 3) ProcManager+托盘 4) 消费更新标记
  5) 冷启动接管 6) 面板注入桥接 + 主窗口 7) config 就绪 → 1s 后自动 start_all
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_paths import (  # noqa: E402
    APP_NAME,
    LOG_DIR,
    config_is_ready,
)
from probes import create_console_flags  # noqa: E402

UPDATE_CHECK_INTERVAL = 6 * 3600


def _setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log(msg):
        line = "[{}] {}".format(time.strftime("%m-%d %H:%M:%S"), msg)
        try:
            print(line)
        except OSError:
            pass
        try:
            with open(LOG_DIR / "launcher.log", "a", encoding="utf-8", errors="ignore") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    sys.excepthook = lambda *a: log("崩溃: " + "".join(traceback.format_exception(*a)))
    return log


def _panel_html():
    """读面板原型并注入两段：<base>（让相对 logo 生效）+ 桥接脚本（</body> 前，后于全部演示脚本执行）。
    桥接脚本 = launcher_src/panel_bridge.js（旧桥+全量实化），frozen 时从 _MEIPASS/panel 取。"""
    from config_paths import IS_FROZEN, ROOT

    if IS_FROZEN:
        src = Path(getattr(sys, "_MEIPASS", "")) / "panel" / "panel.html"
        base_dir = Path(getattr(sys, "_MEIPASS", "")) / "panel"
        bridge_js = base_dir / "panel_bridge.js"
    else:
        src = ROOT / "Moui面板原型.html"
        base_dir = ROOT
        bridge_js = ROOT / "launcher_src" / "panel_bridge.js"
    html = src.read_text(encoding="utf-8")
    base_tag = '<base href="{}">'.format(base_dir.resolve().as_uri() + "/")
    js = bridge_js.read_text(encoding="utf-8") if bridge_js.exists() else \
        "console.error('panel_bridge.js 缺失: {}');".format(bridge_js)
    bridge = "<script>\n{}\n</script>\n</body>".format(js)
    if "</head>" in html:
        html = html.replace("</head>", base_tag + "\n</head>", 1)
    else:
        html = base_tag + html
    # 品牌 logo 内联（真机实锤 2026-10-30：<base> 指向 file:// 被 WebView2 安全策略拦，logo 裂图）——
    # 缩 64px 转 data URI，顺带省一次盘读
    logo = base_dir / "Moui_logo.png"
    if logo.exists():
        try:
            import base64 as _b64
            import io as _io

            from PIL import Image as _Image

            im = _Image.open(logo).convert("RGBA").resize((64, 64), _Image.LANCZOS)
            _buf = _io.BytesIO()
            im.save(_buf, "PNG")
            html = html.replace('src="Moui_logo.png"',
                                'src="data:image/png;base64,' + _b64.b64encode(_buf.getvalue()).decode() + '"')
        except Exception:
            pass
    html = html.replace("</body>", bridge, 1)
    return html


def run_wizard(log):
    """重新配置：临时控制台拉 config_manager.py（坑⑥，唯一黑框例外）。"""
    from config_paths import MAIBOT_DIR, RUNTIME_PYTHON
    import subprocess

    log("拉起配置向导（临时控制台窗口）…")
    subprocess.run(
        [str(RUNTIME_PYTHON), "config_manager.py"],
        cwd=str(MAIBOT_DIR),
        creationflags=create_console_flags(),
    )


def _raise_existing_window():
    """坑⑩：第二实例发唤醒信号后退出。"""
    import ctypes

    handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, "Global\\MoUIMax.ShowRequest")
    if handle:
        ctypes.windll.kernel32.SetEvent(handle)
        ctypes.windll.kernel32.CloseHandle(handle)


class LauncherApi:
    """pywebview js_api 桥：面板 JS 的一切真实操作都走这里（fail-open：永不抛给 JS）。"""

    def __init__(self, pm, tray, log, win_holder):
        self.pm = pm
        self.tray = tray
        self.log = log
        self.win_holder = win_holder          # {"win": webview.Window}
        self._evt_offset = 0
        self._updating = False
        from kbridge import KBridge

        self.kb = KBridge(pm, log)            # K面板代理 + 配置手术 + 重启编排
        self._bot_up_since = 0                # bot 本次上线时刻（uptime 用）

    # ---- K面板/配置透传（panel_bridge.js 消费；KBridge 内部全 fail-open） ----
    def kget(self, path):
        return self.kb.kget(str(path))

    def kpost(self, path, body=None):
        return self.kb.kpost(str(path), body)

    def test_provider(self, base_url, key):
        return self.kb.test_provider(str(base_url or ""), str(key or ""))

    def cfg_get(self):
        return self.kb.cfg_get()

    def cfg_file(self, name):
        return self.kb.cfg_file(str(name))

    def cfg_save(self, payload):
        return self.kb.cfg_save(payload)

    def env_set(self, pairs):
        return self.kb.env_set(pairs or {})

    def persona_get(self):
        return self.kb.persona_get()

    def persona_save(self, fields):
        return self.kb.persona_save(fields or {})

    def wizard_save(self, qq, key, base_url="", ds_key="", style="tsundere", toggles=None):
        return self.kb.wizard_save(qq, key, base_url, ds_key, style, toggles)

    def providers_save(self, cards):
        return self.kb.providers_save(cards or [])

    def flow_status(self):
        return self.kb.flow_status()

    def day_stats(self):
        return self.kb.day_stats()

    def qr_status(self):
        return self.kb.qr_status()

    def logo_b64(self):
        """品牌 logo 走 API 桥直供（真机实锤 2026-10-30：html= 页面里 <base> file:// 与内联 data URI
        都被 WebView2 安全策略拦，logo 裂图）——pywebview 宿主对象调用不经过资源加载，稳。"""
        try:
            import base64 as _b64
            import io as _io

            from config_paths import IS_FROZEN, ROOT

            logo = (Path(getattr(sys, "_MEIPASS", "")) / "panel" / "Moui_logo.png") if IS_FROZEN \
                else (ROOT / "Moui_logo.png")
            if not logo.exists():
                return ""
            try:
                from PIL import Image as _Image

                im = _Image.open(logo).convert("RGBA").resize((64, 64), _Image.LANCZOS)
                _buf = _io.BytesIO()
                im.save(_buf, "PNG")
                return _b64.b64encode(_buf.getvalue()).decode()
            except Exception:
                return _b64.b64encode(logo.read_bytes()).decode()
        except Exception:
            return ""

    def _ensure(self):
        """启动 bot 前的环境兜底（EULA 自动确认 + 空 key 占位），幂等可重复调用。"""
        try:
            self.kb.ensure_runnable()
        except Exception:
            pass

    # ---- 启停 ----
    def start_all(self):
        self._ensure()
        self.pm.start_all()
        return True

    def stop_all(self):
        self.pm.stop_all()
        return True

    def start_service(self, name):
        if str(name) == "bot":
            self._ensure()
        self.pm.start_service(str(name))
        return True

    def finish_wizard(self):
        """面板首启向导「启动她」：真实启动全家桶。"""
        self.log("面板向导完成 → 启动全家桶")
        self._ensure()
        self.pm.start_all()
        return True

    # ---- 状态 ----
    def status(self):
        try:
            bot = self.pm.state.get("bot", "stopped")
            now = time.time()
            if bot == "running":
                if not self._bot_up_since:
                    self._bot_up_since = now
            else:
                self._bot_up_since = 0
            return {
                "mongod": self.pm.state.get("mongod", "stopped"),
                "adapter": self.pm.state.get("adapter", "stopped"),
                "napcat": self.pm.state.get("napcat", "stopped"),
                "bot": bot,
                "version": __import__("config_paths").read_version(),
                "updating": self._updating,
                "uptime": int(now - self._bot_up_since) if self._bot_up_since else 0,
                "wizard": not config_is_ready(),   # 面板向导显隐旗标
            }
        except Exception:
            return {}

    # ---- 日志（日志页 #term） ----
    def logs(self, stream="MaiCore", lines=120):
        try:
            from config_paths import MAIBOT_DIR, SUB_LOG

            mapping = {
                # MaiCore 日志按模块分目录滚动（logs/main/2026-10-30.log），没有 maibot.log——真机实锤
                "MaiCore": MAIBOT_DIR / "logs" / "main" / (time.strftime("%Y-%m-%d") + ".log"),
                "适配器": LOG_DIR / SUB_LOG["adapter"],
                "启动器": LOG_DIR / "launcher.log",
                "NapCat": LOG_DIR / SUB_LOG["napcat"],
                "MongoDB": MAIBOT_DIR / "mongodb" / "logs" / "mongod.log",
            }
            f = mapping.get(stream) or (LOG_DIR / "launcher.log")
            if not f.exists():
                return "（{} 暂无日志）".format(stream)
            with open(f, "rb") as fh:
                fh.seek(max(0, f.stat().st_size - 65536))
                raw = fh.read().decode("utf-8", errors="ignore")
            rows = [ln for ln in raw.splitlines() if ln.strip()]
            return "\n".join(rows[-int(lines):])
        except Exception as exc:
            return "日志读取失败: {}".format(exc)

    # ---- 她的大脑事件（events.jsonl tail → 面板神经网） ----
    def poll_events(self):
        try:
            from config_paths import EVENTS_FILE

            if not EVENTS_FILE.exists():
                return []
            size = EVENTS_FILE.stat().st_size
            if size < self._evt_offset:      # 按天轮转
                self._evt_offset = 0
            if size == self._evt_offset:
                return []
            out = []
            with open(EVENTS_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                fh.seek(self._evt_offset)
                block = fh.read()
                self._evt_offset = fh.tell()
            for ln in block.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    ev = __import__("json").loads(ln)
                except ValueError:
                    continue
                out.append({"node": ev.get("node", ""), "stream_id": ev.get("stream_id", ""),
                            "detail": ev.get("detail", {})})
            if len(out) > 60:                # 积压过多只回放最新 60 条，防糊屏
                out = out[-60:]
            return out
        except Exception:
            return []

    # ---- 更新（更新页 checkUpdate 覆写） ----
    def check_update(self):
        try:
            import updater

            info = updater.check_update(log=self.log)
            if info["status"] == "available":
                threading.Thread(target=self._download_update, args=(info,), daemon=True).start()
            return info
        except Exception as exc:
            return {"status": "error", "notes": str(exc)}

    def _download_update(self, info):
        self._updating = True
        try:
            import updater

            setup = updater.download_update(info, log=self.log)
            updater.apply_update(setup, self.pm, log=self.log)
            time.sleep(1.5)
            os._exit(0)                      # launcher 退出，分离助手接棒
        except Exception as exc:
            self.log("更新失败: {}".format(exc))
            self._updating = False
            try:
                self.win_holder["win"].evaluate_js(
                    "window.__updFail && __updFail({})".format(
                        __import__("json").dumps(str(exc), ensure_ascii=False)))
            except Exception:
                pass

    def update_progress(self):
        return {"updating": self._updating}

    # ---- 其他 ----
    def reconfigure(self):
        threading.Thread(target=run_wizard, args=(self.log,), daemon=True).start()
        return True

    def backup(self):
        self.pm.backup_now()
        return True

    def compass(self):
        self.pm.open_compass()
        return True

    def open_dashboard(self):
        """开发者 K 面板（老大拍板 2026-10-30）：应用内新窗口打开 MaiCore /dashboard，
        供开发者观察原生 K 面板数据；她没上线时 fail-open 提示，绝不炸面板。"""
        try:
            import urllib.request as _u

            _u.urlopen("http://127.0.0.1:8000/dashboard", timeout=2)
        except Exception:
            return {"ok": False, "error": "她还没上线（MaiCore 8000 不可达）——先让她上线再打开 K 面板"}
        try:
            import webview as _w

            _w.create_window(
                "墨忆MOUI · K面板（开发者）", "http://127.0.0.1:8000/dashboard",
                width=1280, height=860, background_color="#141419")
            return {"ok": True, "msg": "K 面板已在应用内窗口打开"}
        except Exception as exc:
            return {"ok": False, "error": "窗口打开失败：{}".format(exc)}

    def quit_app(self):
        self._quitting = True
        threading.Thread(target=self._quit, daemon=True).start()
        return True

    def _quit(self):
        try:
            self.pm.stop_all()
        except Exception:
            pass
        deadline = time.time() + 25
        while time.time() < deadline and self.pm._any_running():
            time.sleep(0.5)
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.win_holder["win"].destroy()
        except Exception:
            pass
        os._exit(0)


def main():
    log = _setup_logger()
    log("=" * 50)
    log("{} 启动器启动（webview-first，dev={}）".format(APP_NAME, not getattr(sys, "frozen", False)))

    from single_instance import SingleInstance

    si = SingleInstance()
    if not si.acquire():
        log("已有实例在运行 → 唤醒后退出")
        _raise_existing_window()
        return

    import webview

    from proc_manager import ProcManager
    from tray import Tray

    win_holder = {"win": None}
    pm = ProcManager(on_notify=lambda msg: None)   # notify 接线在 tray 建好后补
    tray_holder = {}
    tray = Tray(pm, run_wizard=lambda: run_wizard(log), win_holder=win_holder)
    tray_holder["tray"] = tray
    pm.on_notify = tray.notify

    api = LauncherApi(pm, tray, log, win_holder)
    tray.api = api

    # 消费更新标记（E4 第 5 条）
    import updater

    flag = updater.consume_update_flag(log)
    if flag:
        log("本次启动来自更新：{} → {}".format(flag[0], flag[1]))

    # 冷启动接管（六审②）
    pm.takeover()

    # 主窗口 = 面板本体（webview 主线程跑事件循环，不再有 tkinter）
    html = _panel_html()
    win = webview.create_window(
        APP_NAME,
        html=html,
        js_api=api,
        width=1440, height=920, min_size=(1100, 720),
        background_color="#141419",
    )
    win_holder["win"] = win

    # 关窗 = 缩托盘（老大真机反馈：点 X 应进后台，不该整个退出）。
    # closing 返回 False 阻止销毁；真正退出走托盘右键「退出」（stop_all 后 destroy）。
    def _on_closing():
        if getattr(api, "_quitting", False):
            return True   # 托盘「退出」流程中，放行真正销毁
        try:
            win.hide()
            tray.notify("墨忆缩到托盘了 · 她继续在线，双击托盘图标唤回面板")
        except Exception:
            pass
        return False

    try:
        win.events.closing += _on_closing
    except Exception:
        pass

    tray.run_detached()

    # 单实例唤醒监听：第二实例信号 → 提到前台
    import ctypes

    ev = ctypes.windll.kernel32.CreateEventW(None, False, False, "Global\\MoUIMax.ShowRequest")

    def listen_show():
        while True:
            if ev and ctypes.windll.kernel32.WaitForSingleObject(ev, 1000) == 0:
                try:
                    win.show()
                except Exception:
                    pass

    threading.Thread(target=listen_show, daemon=True).start()

    # config 就绪 → 自动拉起全家桶（§〇：双击即上线，老大拍板取消一键启动步骤）
    def autostart():
        time.sleep(1.2)
        if config_is_ready():
            log("config 就绪 → 自动启动全家桶")
            pm.start_all()
        else:
            log("config 未就绪 → 面板首启向导接管")

    threading.Thread(target=autostart, daemon=True).start()

    # 后台更新探测（有新版只气泡提醒；更新页里点检查才动手）
    def update_watch():
        time.sleep(30)
        while True:
            try:
                if config_is_ready():
                    info = updater.check_update(log=log)
                    if info["status"] == "available":
                        tray.notify("发现新版本 {}！在面板「更新」页点「检查更新」即可升级".format(info["version"]))
            except Exception as exc:
                log("更新探测异常: {}".format(exc))
            time.sleep(UPDATE_CHECK_INTERVAL)

    threading.Thread(target=update_watch, daemon=True).start()

    log("启动器就绪（面板即主界面）")
    webview.start(gui="edgechromium", debug=False)
    si.release()


if __name__ == "__main__":
    main()
