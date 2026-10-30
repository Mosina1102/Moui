# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 进程托管核心（E3 第 2/3/4 条 + v2.4 控制台收编）
======================================================================
拓扑（§二）：mongod → adapter(8095) → NapCat(进程+连接双判定) → bot.py(8000)
停止严格反序；PID 记 logs/launcher/pids.json（三审④）；冷启动接管孤儿（六审②）；
守护 bot+adapter（三审③）；单实例互斥在外层（坑⑩）。
"""
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

from config_paths import (
    ADAPTER_DIR,
    COMPASS_EXE,
    DISK_MIN_FREE,
    LOG_DIR,
    LOG_ROTATE_BYTES,
    MAIBOT_DIR,
    MONGO_DBPATH,
    MONGOD_EXE,
    NAPCAT_DIR,
    NAPCAT_BOOT_EXE,
    PIDS_FILE,
    PORT_ADAPTER,
    PORT_MONGOD,
    PORT_MAICORE,
    RUNTIME_PYTHON,
    SERVICE_LABELS,
    SERVICES,
    SUB_LOG,
    read_maicore_port,
)
from probes import (
    adapter_has_napcat_connection,
    create_console_flags,
    create_no_window_flags,
    disk_free_bytes,
    env_for_children,
    find_image_pids,
    is_our_python,
    kill_image_under_dir,
    napcat_process_alive,
    probe_port,
    taskkill_tree,
    terminate_graceful,
    user_qq_running,
    wait_port,
)

# 状态常量（灯：绿=运行 黄=启动中 红=停止/异常 灰=未托管）
ST_STOPPED, ST_STARTING, ST_RUNNING, ST_ERROR = "stopped", "starting", "running", "error"

WATCHDOG_ENABLED = True        # 守护开关（默认开，E3 第 4 条）
WATCHDOG_CHECK = 20            # 守护巡检间隔
WATCHDOG_BACKOFF = 60          # 三审③：60s 起步退避
WATCHDOG_MAX_RETRY = 3         # 连续崩溃次数上限，超过转 ERROR 交给用户
NAPCAT_FIRST_WAIT = 120        # NapCat 首次登录等待（扫码是人工的，风险 5）
NAPCAT_NORMAL_WAIT = 60
DISK_CHECK_INTERVAL = 600      # 五审③：10 分钟一查


class ProcManager:
    def __init__(self, on_state=None, on_notify=None, on_log=None, tk_after=None):
        self.lock = threading.RLock()
        self.state = {s: ST_STOPPED for s in SERVICES}
        self.pids = {s: None for s in SERVICES}
        self.procs = {s: None for s in SERVICES}   # Popen 句柄（接管孤儿时为 None）
        self.adopted = {s: False for s in SERVICES}
        self._wd_retries = {s: 0 for s in SERVICES}
        self._disk_warned = False
        self.on_state = on_state or (lambda *a: None)
        self.on_notify = on_notify or (lambda *a: None)
        self.on_log = on_log or (lambda *a: None)
        self.tk_after = tk_after                    # UI 线程封送（customtkinter after）
        self._stop_flags = set()
        threading.Thread(target=self._watchdog_loop, daemon=True).start()
        threading.Thread(target=self._disk_watch_loop, daemon=True).start()

    # ---------------- 状态与日志 ----------------

    def _set(self, name, st):
        with self.lock:
            if self.state.get(name) == st:
                return
            self.state[name] = st
        self._fire(self.on_state, name, st)

    def _fire(self, fn, *a):
        if self.tk_after:
            try:
                self.tk_after(lambda: fn(*a))
                return
            except Exception:
                pass
        fn(*a)

    def _log(self, msg):
        self._fire(self.on_log, msg)

    def _notify(self, msg):
        self._fire(self.on_notify, msg)

    # ---------------- PID 持久化（三审④） ----------------

    def _save_pids(self):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            data = {"ts": time.time(), "pids": {k: self.pids[k] for k in SERVICES}}
            PIDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # fail-open：PID 落盘失败不影响运行，只是安装器少一层兜底

    # ---------------- 子进程日志（替代黑框） ----------------

    def _open_sub_log(self, name):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / SUB_LOG[name]
        try:
            if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
                path.rename(path.with_suffix(".log.old"))
        except OSError:
            pass
        return open(path, "a", encoding="utf-8", errors="ignore")

    # ---------------- 冷启动接管（六审②） ----------------

    def takeover(self):
        """读 pids.json 探测存活子进程并接管：灯复绿、不重复拉起、一键停止照样管。"""
        try:
            data = json.loads(PIDS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        pids = data.get("pids", {})
        for name, pid in pids.items():
            if not pid or name not in SERVICES:
                continue
            alive, our = self._alive_and_ours(name, pid)
            if not alive:
                continue
            with self.lock:
                self.pids[name] = pid
                self.procs[name] = None
                self.adopted[name] = True
                self.state[name] = ST_RUNNING
            self._fire(self.on_state, name, ST_RUNNING)
            self._log("[接管] {}（PID {}）已在运行，本次不重复拉起".format(SERVICE_LABELS[name], pid))

    def _alive_and_ours(self, name, pid):
        """PID 存活 且 归属校验：mongod/napcat 看镜像名，python 类看完整路径（三审④）。"""
        try:
            proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, OSError):
            return False, False
        try:
            exe = (proc.exe() or "").lower()
            pname = (proc.name() or "").lower()
        except (psutil.AccessDenied, OSError):
            return True, True   # 存在但探不动：保守当自己的（接管比双启动安全）
        if name == "mongod":
            ok = pname == "mongod.exe"
        elif name == "napcat":
            ok = pname in ("qq.exe", "napcatwinbootmain.exe")
        else:  # adapter / bot 都是 runtime python
            ok = pname == "python.exe" and exe == str(RUNTIME_PYTHON).lower()
        return True, ok

    # ---------------- 单独启停（v2.4 收编控制台 2~5 项） ----------------

    def start_service(self, name):
        threading.Thread(target=self._start_one, args=(name,), daemon=True).start()

    def _start_one(self, name):
        with self.lock:
            if self.state[name] in (ST_RUNNING, ST_STARTING):
                return
        self._stop_flags.discard(name)
        try:
            if name == "mongod":
                self._start_mongod()
            elif name == "adapter":
                self._start_adapter()
            elif name == "napcat":
                self._start_napcat()
            elif name == "bot":
                self._start_bot()
        except Exception as exc:   # fail-open：启动异常转红灯并通知，绝不拖死启动器
            self._log("[错误] 启动 {} 失败: {}".format(name, exc))
            self._set(name, ST_ERROR)
            self._notify("{} 启动失败：{}".format(SERVICE_LABELS.get(name, name), exc))

    def _popen(self, name, cmd, cwd, console=False):
        """统一 Popen：零黑框 + 日志重定向 + PID 落盘。"""
        fh = self._open_sub_log(name)
        flags = create_console_flags() if console else create_no_window_flags()
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags, env=env_for_children(),
        )
        with self.lock:
            self.procs[name] = proc
            self.pids[name] = proc.pid
            self.adopted[name] = False
        self._save_pids()
        return proc

    def _start_mongod(self):
        if probe_port(PORT_MONGOD):
            self._log("[MongoDB] 27017 已有监听，跳过重复启动")
            self._set("mongod", ST_RUNNING)
            return
        self._set("mongod", ST_STARTING)
        self._log("[MongoDB] 启动 mongod…")
        self._popen(
            "mongod",
            [str(MONGOD_EXE), "--dbpath=" + str(MONGO_DBPATH),
             "--logpath=" + str(MAIBOT_DIR / "mongodb" / "logs" / "mongod.log"),
             "--port", str(PORT_MONGOD)],
            cwd=MAIBOT_DIR,
        )
        if wait_port(PORT_MONGOD, 30):
            self._set("mongod", ST_RUNNING)
            self._log("[MongoDB] 27017 就绪")
        else:
            self._set("mongod", ST_ERROR)
            self._notify("MongoDB 没起来（端口 27017 未监听），看看 mongodb\\logs\\mongod.log")

    def _start_adapter(self):
        if probe_port(PORT_ADAPTER):
            self._log("[适配器] 8095 已有监听，跳过重复启动")
            self._set("adapter", ST_RUNNING)
            return
        self._set("adapter", ST_STARTING)
        self._log("[适配器] 启动 main.py…")
        self._popen("adapter", [str(RUNTIME_PYTHON), "main.py"], cwd=ADAPTER_DIR)
        if wait_port(PORT_ADAPTER, 60):
            self._set("adapter", ST_RUNNING)
            self._log("[适配器] 8095 就绪")
        else:
            self._set("adapter", ST_ERROR)
            self._notify("适配器没起来（8095 未监听），日志在 logs\\launcher\\adapter.log")

    def _ensure_napcat_wire(self):
        """NapCat 登录后按号生成 onebot11_<qq>.json，出厂 network 全空 → 适配器永远等不到连接
        （老大真机实锤"连接不上napcat"的根因）。拉起前把每个配置文件的 websocketClients
        补上指向适配器 8095 的正向 ws，用户零手配。返回 True = 本次实际补过配置。"""
        import json as _json

        if not NAPCAT_DIR.exists():
            return False
        rewired = False
        for cfg in NAPCAT_DIR.glob("versions/*/resources/app/napcat/config/onebot11_*.json"):
            try:
                data = _json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                continue
            net = data.get("network") or {}
            clients = net.get("websocketClients") or []
            if any(c.get("enable") and "8095" in str(c.get("url", "")) for c in clients):
                continue
            net["websocketClients"] = [{
                "name": "Moui", "enable": True, "url": "ws://localhost:8095",
                "messagePostFormat": "array", "reportSelfMessage": False,
                "token": "", "debug": False, "heartInterval": 30000, "reconnectInterval": 5000,
            }]
            data["network"] = net
            try:
                cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self._log("[NapCat] 已把 {} 的 ws 出站接上适配器（ws://localhost:8095）".format(cfg.name))
                rewired = True
            except OSError as exc:
                self._log("[NapCat] 接线 {} 失败: {}".format(cfg.name, exc))
        return rewired

    def _known_bot_qq(self):
        """她的 QQ 号：bot_config 的真实号优先，兜底 napcat 历史登录记录（onebot11_<qq>.json 文件名）。
        用于 NapCat -q 快速登录：登录态还在就免扫码直接上线，只有首次才需要扫码。"""
        import re as _re

        try:
            cfg = MAIBOT_DIR / "config" / "bot_config.toml"
            if cfg.exists():
                m = _re.search(r'^\s*qq\s*=\s*["\']?(\d+)', cfg.read_text(encoding="utf-8", errors="ignore"), _re.M)
                if m and m.group(1) and m.group(1) != "1145141919810":
                    return m.group(1)
        except OSError:
            pass
        try:
            for p in NAPCAT_DIR.glob("versions/*/resources/app/napcat/config/onebot11_*.json"):
                m = _re.search(r"onebot11_(\d+)\.json$", p.name)
                if m:
                    return m.group(1)
        except OSError:
            pass
        return ""

    def _start_napcat(self, console=None):
        """NapCat：首次登录（无 onebot11_*.json）给临时控制台扫码，日常零黑框。
        已知她的 QQ 时带 -q 快速登录——登录态还在就免扫码，日常零打扰。"""
        self._set("napcat", ST_STARTING)
        rewired = self._ensure_napcat_wire()   # 返回 True = 本次补了 ws 出站配置
        if console is None:
            console = not any((NAPCAT_DIR / "versions").glob("*/resources/app/napcat/config/onebot11_*.json"))
        bot_qq = self._known_bot_qq()
        napcat_cmd = [str(NAPCAT_BOOT_EXE)] + (["-q", str(bot_qq)] if bot_qq else [])
        if not napcat_process_alive():
            if user_qq_running():
                self._notify("检测到你登着的 QQ，Moui 要用自己专属的 QQ 号，先把你自己那个 QQ 退一下哦")
            self._log("[NapCat] 拉起 NapCatWinBootMain{}{}…".format(
                "（快速登录 {}）".format(bot_qq) if bot_qq else "（首登控制台扫码）",
                "，先重启使 ws 接线生效" if rewired else ""))
            self._popen("napcat", napcat_cmd, cwd=NAPCAT_DIR, console=console)
        elif rewired and not adapter_has_napcat_connection():
            # 老进程吃不到刚补的 ws 出站配置（配置只在启动时读）→ 重启她一次，新配置即生效
            self._log("[NapCat] 已补 ws 出站配置但进程是旧配置启动的 → 重启 NapCat 使其生效")
            self._kill_bot_qq_children(0, True)
            kill_image_under_dir("NapCatWinBootMain.exe", NAPCAT_DIR)
            kill_image_under_dir("QQ.exe", NAPCAT_DIR)
            time.sleep(2.0)
            self._popen("napcat", napcat_cmd, cwd=NAPCAT_DIR, console=False)
        else:
            self._log("[NapCat] 检测到已有 NapCat 进程，跳过拉起")
        wait = NAPCAT_FIRST_WAIT if console else NAPCAT_NORMAL_WAIT
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.state.get("napcat") == ST_STOPPED and "napcat" in self._stop_flags:
                return   # 用户点了停止
            if napcat_process_alive() and adapter_has_napcat_connection():
                self._set("napcat", ST_RUNNING)
                self._log("[NapCat] 就绪（进程在 + 适配器侧有活跃连接）")
                return
            time.sleep(2)
        # 未就绪：首登场景=等扫码（黄灯挂着不算失败）；日常场景=异常
        if napcat_process_alive():
            self._log("[NapCat] 进程在但还没有活跃连接（首次使用请在弹出的窗口扫码登录 QQ）")
        else:
            self._set("napcat", ST_ERROR)
            self._notify("NapCat 没起来，检查日志 logs\\launcher\\napcat.log")

    def _start_bot(self):
        port = read_maicore_port()
        if probe_port(port):
            self._log("[主程序] {} 端口已在监听，跳过重复启动".format(port))
            self._set("bot", ST_RUNNING)
            self._notify("她上线了")
            return
        self._set("bot", ST_STARTING)
        self._log("[主程序] 启动 bot.py…")
        self._popen("bot", [str(RUNTIME_PYTHON), "bot.py"], cwd=MAIBOT_DIR)
        if wait_port(port, 90):
            self._set("bot", ST_RUNNING)
            self._notify("她上线了")
        else:
            self._set("bot", ST_ERROR)
            self._notify("主程序没起来（{} 端口未就绪），日志在 logs\\launcher\\bot.log".format(port))

    # ---------------- 一键全家桶 ----------------

    def start_all(self):
        def chain():
            if self._any_running():
                self._log("[一键启动] 检测到已有服务在跑，先补齐缺失的即可")
            self._start_one("mongod")
            self._start_one("adapter")
            self._start_one("napcat")
            self._start_one("bot")
        threading.Thread(target=chain, daemon=True).start()

    def stop_all(self):
        def chain():
            for name in ("bot", "napcat", "adapter", "mongod"):
                self._stop_one(name)
            self._notify("全家桶已停止")
        threading.Thread(target=chain, daemon=True).start()

    def stop_service(self, name):
        threading.Thread(target=self._stop_one, args=(name,), daemon=True).start()

    def _stop_one(self, name):
        self._stop_flags.add(name)
        with self.lock:
            pid, proc, adopted = self.pids[name], self.procs[name], self.adopted[name]
        if not pid:
            self._set(name, ST_STOPPED)
            return
        self._log("[停止] {}（PID {}）…".format(SERVICE_LABELS[name], pid))
        if name == "mongod":
            self._stop_mongod(pid, adopted, proc)
        else:
            if name == "napcat":
                # 先按【父子关系】杀我们拉起的 bot QQ——napcat 目录的 QQ.exe 只是 1.6MB stub，
                # 真身跑在系统 QQ 安装目录（与用户 QQ 同路径），必须按进程树找，绝不能按镜像名扫。
                # 老大真机实锤 2026-10-30：退出时把全电脑的 QQ 都带走了（NapCat 清场按镜像名扫的锅）。
                self._kill_bot_qq_children(pid, adopted)
            if adopted or proc is None:
                terminate_graceful(pid)
            else:
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except Exception:
                    taskkill_tree(pid)
            if name == "napcat":
                # 收尾 stub 本体与 boot 引导（都在 napcat 目录内，路径限定兜底）
                kill_image_under_dir("NapCatWinBootMain.exe", NAPCAT_DIR)
                kill_image_under_dir("QQ.exe", NAPCAT_DIR)
        with self.lock:
            self.pids[name] = None
            self.procs[name] = None
            self.adopted[name] = False
        self._save_pids()
        self._set(name, ST_STOPPED)

    def _kill_bot_qq_children(self, pid, adopted):
        """按父子关系精确杀我们拉起的 bot QQ（NapCatWinBootMain 的后代里名字叫 QQ.exe 的）。
        用户自己开的 QQ 不在我们的进程树里，绝不受影响。"""
        try:
            import psutil

            root_pid = pid
            if adopted or not root_pid:
                root_pid = 0
                prefix = str(NAPCAT_DIR).lower()
                for p in psutil.process_iter(["pid", "name", "exe"]):
                    try:
                        if (p.info["name"] or "").lower() == "napcatwinbootmain.exe" and \
                           (p.info["exe"] or "").lower().startswith(prefix):
                            root_pid = p.info["pid"]
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                        continue
            if not root_pid:
                return
            try:
                root = psutil.Process(root_pid)
            except psutil.NoSuchProcess:
                return
            for kid in root.children(recursive=True):
                try:
                    if (kid.name() or "").lower() == "qq.exe":
                        kid.kill()
                        self._log("[NapCat] 已停她的 QQ（PID {}，仅限她的进程树）".format(kid.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    continue
        except Exception as exc:
            self._log("[NapCat] 精确停 QQ 异常（忽略）: {}".format(exc))

    def _stop_mongod(self, pid, adopted, proc):
        """优雅：第二实例 --shutdown（走 mongod.lock，免管理员）；失败按 PID 杀（坑⑭）。"""
        try:
            subprocess.run(
                [str(MONGOD_EXE), "--shutdown", "--dbpath=" + str(MONGO_DBPATH)],
                capture_output=True, timeout=30, cwd=str(MAIBOT_DIR),
                creationflags=create_no_window_flags(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if probe_port(PORT_MONGOD, timeout=0.4):
            if adopted or proc is None:
                terminate_graceful(pid)
            else:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    taskkill_tree(pid)
        if probe_port(PORT_MONGOD, timeout=0.4):
            kill_image_under_dir("mongod.exe", MONGOD_EXE.parent)

    def _any_running(self):
        with self.lock:
            return any(v == ST_RUNNING for v in self.state.values())

    # ---------------- 守护（三审③：bot + adapter 双守） ----------------

    def _watchdog_loop(self):
        if not WATCHDOG_ENABLED:
            return
        while True:
            time.sleep(WATCHDOG_CHECK)
            for name in ("bot", "adapter"):
                with self.lock:
                    pid, st, adopted, proc = self.pids[name], self.state[name], self.adopted[name], self.procs[name]
                if st != ST_RUNNING or not pid:
                    continue
                alive = (
                    (proc is not None and proc.poll() is None)
                    if not adopted
                    else self._alive_and_ours(name, pid)[0]
                )
                if alive:
                    self._wd_retries[name] = 0
                    continue
                if "napcat" in self._stop_flags and name == "adapter":
                    continue   # 正在停全家桶，别抢戏
                retries = self._wd_retries[name] + 1
                self._wd_retries[name] = retries
                if retries > WATCHDOG_MAX_RETRY:
                    self._set(name, ST_ERROR)
                    self._notify("{} 反复崩溃（{} 次），不再自动拉起，看看日志吧".format(SERVICE_LABELS[name], retries))
                    continue
                self._log("[守护] {} 意外退出，{}s 后自动拉起（第 {} 次）".format(SERVICE_LABELS[name], WATCHDOG_BACKOFF, retries))
                self._notify("{} 掉线了，{}秒后自动拉起，别慌".format(SERVICE_LABELS[name], WATCHDOG_BACKOFF))
                self._set(name, ST_STARTING)
                threading.Timer(WATCHDOG_BACKOFF, self._start_one, args=(name,)).start()

    # ---------------- 磁盘预警（五审③） ----------------

    def _disk_watch_loop(self):
        while True:
            free = disk_free_bytes(MAIBOT_DIR)
            if free < DISK_MIN_FREE and not self._disk_warned:
                self._disk_warned = True
                self._notify("磁盘剩余空间不到 2GB 了——数据库和备份可能写不进去，清理一下 C 盘或卸载点东西吧")
            elif free >= DISK_MIN_FREE:
                self._disk_warned = False
            time.sleep(DISK_CHECK_INTERVAL)

    # ---------------- 立即备份（v2.4 收编控制台第 8 项） ----------------

    def backup_now(self):
        def work():
            try:
                dst_root = MAIBOT_DIR / "data" / "backup" / "mongodb_{}".format(
                    datetime.now().strftime("%Y%m%d_%H%M%S"))
                dst_root.mkdir(parents=True, exist_ok=True)
                self._log("[备份] 开始复制 mongodb\\db → {}".format(dst_root.name))
                shutil.copytree(MONGO_DBPATH, dst_root / "db", dirs_exist_ok=True)
                self._notify("数据库备份完成：data\\backup\\{}".format(dst_root.name))
            except Exception as exc:
                self._notify("备份失败：{}".format(exc))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- Compass（收编第 11 项） ----------------

    def open_compass(self):
        if COMPASS_EXE.exists():
            subprocess.Popen([str(COMPASS_EXE)], cwd=str(COMPASS_DIR),
                             creationflags=create_no_window_flags())
        else:
            self._notify("没找到 Compass，看看 MaiBot\\MongoDBCompassPortable 目录还在不在")

    # ---------------- 红灯自诊（六审④） ----------------

    DIAGNOSIS = {
        "mongod": "MongoDB 红灯常见原因：\n· 27017 端口被别的程序占了\n· 磁盘满了数据库写不进去\n· VC++ 运行库缺失（重装一次 Moui 即可补上）",
        "adapter": "适配器红灯常见原因：\n· NapCat 还没登录 QQ\n· 日志报 API key 相关错误 → 打开「重新配置」重填",
        "napcat": "NapCat 红灯常见原因：\n· 你的 QQ 掉线了/需要重新扫码\n· 你自己的 QQ 还开着（QQ 是单实例应用）\n· 杀毒软件拦了 NapCat 的注入组件（加白名单）",
        "bot": "主程序红灯常见原因：\n· API key 不对或服务商限流中\n· MongoDB 没起来\n· 配置文件被改坏了 → 托盘「重新配置」重跑一遍向导",
    }

    def diagnose(self, name):
        self._fire(self.on_notify, self.DIAGNOSIS.get(name, ""))
