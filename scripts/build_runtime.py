#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX · E1 内嵌 Python 便携运行时构建脚本
==================================================
施工卡 E1（exe化策划书 v2.4 §三）：免装 Python 的前提。

流程：
  1. 自举构建机 Python 3.12.8（官方安装器静默装到 .build\\py312，per-user 免管理员，不碰系统）
  2. 下载官方 python-3.12.8-embed-amd64.zip 解压为 dist\\runtime\\
  3. 改写 runtime\\python312._pth（放开 site + 挂 ..\\MaiBot + Lib\\site-packages）
  4. 建 venv → 按 requirements 装依赖（constraints.lock 锁版本，可重复构建）
  5. 合并 venv site-packages → runtime\\Lib\\site-packages（剔除 pyvenv.cfg / Scripts 壳）
  6. 生成出厂配置副本（template → .build\\factory\\，E2 打包用，防实例配置混入）
  7. 写 site-packages 清单并与上次构建 diff（验收：连续两次构建清单 diff 为空）
  8. 验收自检：runtime\\python.exe -c "import fastapi, faiss, igraph" 等

用法：
  python scripts/build_runtime.py [--index-url URL] [--skip-bootstrap] [--only-verify]

铁律对齐（Moui施工总方案 〇章）：常量化置文件头部 / fail-open 自检不阻断主构建 / py_compile 过审。
依赖砍单（老大拍板 2026-10-30）：只砍 gradio（死依赖 audit 首砍）；customtkinter 按方案不进
runtime（PyInstaller 打 launcher 时自带）；matplotlib 实测存活必留（person_info/visualize_graph 在用）。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---------------- 常量区（铁律：参数常量化，附注释） ----------------
PY_VER = "3.12.8"                                   # 运行时版本，E1 锁死
PY_FULL_INSTALLER = "python-{}-amd64.exe".format(PY_VER)      # 构建机自举用完整安装器
PY_EMBED_ZIP = "python-{}-embed-amd64.zip".format(PY_VER)     # 分发用 embeddable 包
PYTHONORG_BASE = "https://www.python.org/ftp/python/{}".format(PY_VER)
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"    # 国内镜像默认，可 --index-url 覆盖
EXCLUDE_PKGS = {"gradio", "customtkinter"}          # 坑⑤：死依赖/launcher 自带，不进 runtime

ROOT = Path(__file__).resolve().parent.parent       # 仓库根（VERSION 所在地）
MAIBOT = ROOT / "MaiBot"                            # 源码树
BUILD = ROOT / ".build"                             # 构建中间区（可整删重来自愈）
DOWNLOADS = BUILD / "downloads"
PY312_DIR = BUILD / "py312"                         # 自举的构建机 Python
VENV_DIR = BUILD / "venv"                           # 依赖安装 venv
FACTORY_DIR = BUILD / "factory"                     # 出厂配置副本输出（E2 打包取用）
MANIFESTS = BUILD / "manifests"                     # site-packages 清单（可重复构建验收）
DIST = ROOT / "dist"                                # 分发物输出
RUNTIME = DIST / "runtime"                          # E1 产物
JUNCTION_MAIBOT = DIST / "MaiBot"                   # dist 布局预演：runtime 的 ..\MaiBot 兄弟目录
VERSION_FILE = ROOT / "VERSION"
CONSTRAINTS_LOCK = ROOT / "constraints.lock"        # 版本锁（坑④：没有 lock 就没有可重复构建）
REQ_SRC = MAIBOT / "requirements.txt"

# ._pth 内容（E1 第 2 条）：embeddable 默认隔离 site-packages，必须放开并挂上 MaiBot 源码路径
PTH_NAME = "python312._pth"
PTH_CONTENT = """python312.zip
.
Lib\\site-packages
..\\MaiBot

import site
"""

# 验收自检导入清单：E1 验收硬指标 fastapi/faiss/igraph + 其余大件健康巡检
VERIFY_IMPORTS = [
    "fastapi", "faiss", "igraph",                    # E1 验收硬指标
    "numpy", "scipy", "pandas", "matplotlib", "pyarrow", "networkx",
    "jieba", "loguru", "maim_message", "quick_algo", "pymongo",
    "uvicorn", "websockets", "PIL", "apscheduler", "openai",
    "tomlkit", "dotenv", "requests", "aiohttp",
]

STEP_TOTAL = 8

# ---------------- 工具函数 ----------------

def log(step, msg):
    print("[E1 {}/{}] {}".format(step, STEP_TOTAL, msg), flush=True)


def run(cmd, **kw):
    """跑子进程，回显命令；失败抛异常带退出码。"""
    print("  $ {}".format(" ".join(str(c) for c in cmd)), flush=True)
    kw.setdefault("check", True)
    return subprocess.run([str(c) for c in cmd], **kw)


def download(url, dest, step):
    """带进度的大文件下载（python.org 直连）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        log(step, "已存在缓存，跳过下载：{}".format(dest.name))
        return
    log(step, "下载 {} → {}".format(url, dest.name))
    req = urllib.request.Request(url, headers={"User-Agent": "MoUIMax-E1-Builder/1.0"})
    started = time.time()
    last_bytes = 0
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            if total and time.time() - started > 5 and fh.tell() - last_bytes > 5 * 1024 * 1024:
                last_bytes = fh.tell()
                pct = int(fh.tell() * 100 / total) if total else 0
                log(step, "  进度 {}% ({:.1f} MB)".format(pct, fh.tell() / 1048576))
    log(step, "下载完成 {:.1f} MB，用时 {:.0f}s".format(dest.stat().st_size / 1048576, time.time() - started))


# ---------------- 构建步骤 ----------------

def bootstrap_build_python(step):
    """步骤 1：自举构建机 Python 3.12.8（per-user 静默装，免管理员不碰系统 PATH）。"""
    exe = PY312_DIR / "python.exe"
    if exe.exists():
        out = subprocess.run([str(exe), "-c", "import sys;print(sys.version.split()[0])"],
                             capture_output=True, text=True)
        ver = (out.stdout or "").strip()
        if ver == PY_VER:
            log(step, "构建机 Python {} 就绪：{}".format(ver, exe))
            return exe
        log(step, "发现旧版本 {}（期望 {}），重建自举目录".format(ver or "?", PY_VER))
        shutil.rmtree(PY312_DIR, ignore_errors=True)

    installer = DOWNLOADS / PY_FULL_INSTALLER
    download("{}/{}".format(PYTHONORG_BASE, PY_FULL_INSTALLER), installer, step)
    log(step, "静默安装到 {}（per-user，免管理员）…".format(PY312_DIR))
    run([
        installer, "/quiet", "InstallAllUsers=0",
        "TargetDir={}".format(PY312_DIR),
        "Include_pip=1", "Include_test=0", "Include_launcher=0",
        "AssociateFiles=0", "Shortcuts=0", "PrependPath=0",
        "Include_doc=0", "Include_dev=0", "Include_tcltk=0", "Include_tools=0",
    ], timeout=900)
    ver = None
    exe = PY312_DIR / "python.exe"
    if exe.exists():
        ver = subprocess.run([str(exe), "-c", "import sys;print(sys.version.split()[0])"],
                             capture_output=True, text=True).stdout.strip()
    if ver != PY_VER:
        # 兜底：本机可能已装过 3.12.8（安装器认为 Present 直接空跑），从 PEP514 注册表找现成安装搬过来
        import winreg
        for hive, view in [(winreg.HKEY_CURRENT_USER, winreg.KEY_READ),
                           (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)]:
            try:
                with winreg.OpenKey(hive, r"Software\Python\PythonCore\3.12\InstallPath", 0, view) as k:
                    found = winreg.QueryValueEx(k, "")[0]
            except OSError:
                continue
            found_exe = Path(found) / "python.exe"
            if found_exe.exists():
                ver = subprocess.run([str(found_exe), "-c", "import sys;print(sys.version.split()[0])"],
                                     capture_output=True, text=True).stdout.strip()
                if ver == PY_VER and not exe.exists():
                    log(step, "PEP514 兜底：发现本机现成 Python {} @ {}，拷贝入 .build".format(ver, found))
                    shutil.copytree(found, PY312_DIR, dirs_exist_ok=True)
                break
    ver = subprocess.run([str(exe), "-c", "import sys;print(sys.version.split()[0])"],
                         capture_output=True, text=True).stdout.strip() if exe.exists() else None
    if ver != PY_VER:
        raise RuntimeError("自举 Python 版本不符：{} != {}".format(ver, PY_VER))
    log(step, "自举完成：{} = Python {}".format(exe, ver))
    return exe


def stage_embeddable(step):
    """步骤 2+3：embeddable 解压为 dist\\runtime 并改写 ._pth。"""
    zip_path = DOWNLOADS / PY_EMBED_ZIP
    download("{}/{}".format(PYTHONORG_BASE, PY_EMBED_ZIP), zip_path, step)

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(RUNTIME)
    (RUNTIME / PTH_NAME).write_text(PTH_CONTENT, encoding="ascii")
    log(step, "embeddable 就绪，{} 已改写（site 放开 + ..\\MaiBot + Lib\\site-packages）".format(PTH_NAME))


def filter_requirements(step):
    """依赖砍单：requirements 原文剔除 EXCLUDE_PKGS，产出 .build\\requirements_runtime.txt。"""
    kept, dropped = [], []
    raw_bytes = REQ_SRC.read_bytes()
    # 编码容错：requirements 可能是 utf-8 / utf-8-sig / utf-16（实测本仓库为 utf-16 BOM）
    for enc in ("utf-8-sig", "utf-16", "gbk", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        name = line.split(";")[0].split("[")[0].split("==")[0].strip().lower()
        if name in EXCLUDE_PKGS:
            dropped.append(line)
        else:
            kept.append(raw)
    out = BUILD / "requirements_runtime.txt"
    out.write_text(
        "# 由 scripts/build_runtime.py 生成（勿手改）：源 requirements.txt 剔除 {}\n".format(sorted(EXCLUDE_PKGS))
        + "\n".join(kept) + "\n",
        encoding="utf-8",
    )
    log(step, "依赖清单就绪：保留 {} 项，砍掉 {}".format(len(kept), dropped))
    return out


def install_deps(step, build_python, req_file, index_url):
    """步骤 4：重建 venv → 装依赖（有 lock 用 lock，首次装完生成 lock）。"""
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    run([build_python, "-m", "venv", VENV_DIR])
    venv_python = VENV_DIR / "Scripts" / "python.exe"

    env = dict(os.environ)
    env.update({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_DEFAULT_TIMEOUT": "120"})
    pip_cmd = [venv_python, "-m", "pip", "install",
               "-r", req_file,
               "--prefer-binary",             # 大件优先 wheel；jieba 等 sdist-only 纯 py 包放行（无需编译器）
               "--no-warn-script-location",
               "--retries", "5",
               "--index-url", index_url]
    if CONSTRAINTS_LOCK.exists():
        log(step, "constraints.lock 在库，锁定版本安装（可重复构建）")
        pip_cmd[6:6] = ["-c", str(CONSTRAINTS_LOCK)]
    else:
        log(step, "首次构建：装完后 pip freeze 生成 constraints.lock 入库（坑④）")
    run(pip_cmd, env=env, timeout=3600)

    if not CONSTRAINTS_LOCK.exists():
        frozen = subprocess.run([str(venv_python), "-m", "pip", "freeze"],
                                capture_output=True, text=True, check=True).stdout
        CONSTRAINTS_LOCK.write_text(
            "# 墨忆MOUI-MAX runtime 版本锁（由 scripts/build_runtime.py 首次构建生成）\n"
            "# 之后一律 pip install -r requirements.txt -c constraints.lock（坑④ 可重复构建）\n"
            + frozen, encoding="utf-8")
        log(step, "constraints.lock 已入库（{} 行）".format(len(frozen.splitlines())))
    return venv_python


def merge_site_packages(step, venv_python):
    """步骤 5：venv site-packages 整目录并入 runtime（E1 第 6 条：只搬纯包目录）。"""
    src = VENV_DIR / "Lib" / "site-packages"
    dst = RUNTIME / "Lib" / "site-packages"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    # 防路径假设泄漏：venv 自身痕迹（pyvenv.cfg 在 venv 根、Scripts 壳不在 site-packages）按定义不会混入，
    # 但历史包可能带 .pth 指向构建机路径——扫描报出来，人工留痕。
    suspicious = []
    for pth in dst.glob("*.pth"):
        try:
            content = pth.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ln in content.splitlines():
            s = ln.strip()
            if s and not s.startswith("#") and ("venv" in s.lower() or ":\\" in s):
                suspicious.append("{} → {}".format(pth.name, s))
    log(step, "site-packages 合并完成（{} 个顶层条目）".format(len(list(dst.iterdir()))))
    if suspicious:
        print("  [警告] 发现可疑 .pth 路径（不影响运行，留痕备查）：")
        for s in suspicious:
            print("    - {}".format(s))


def emit_factory_config(step):
    """步骤 6：出厂配置副本（E1 第 8 条/五审①：template 生成的干净出厂件，构建机实例版绝不进分发物）。"""
    if FACTORY_DIR.exists():
        shutil.rmtree(FACTORY_DIR)
    FACTORY_DIR.mkdir(parents=True)
    pairs = [
        (MAIBOT / "template" / "bot_config_template.toml", "bot_config.toml"),
        (MAIBOT / "template" / "lpmm_config_template.toml", "lpmm_config.toml"),
        (MAIBOT / "template" / "template.env", ".env"),
        (MAIBOT / "MaiBot-Napcat-Adapter" / "template" / "template_config.toml", "adapter_config.toml"),
    ]
    # 分发物完整性检查（三审遗留清账）：漏一个 = 用户首启卡死在自生成失败
    for src, name in pairs:
        if not src.exists():
            raise FileNotFoundError("模板缺失，禁止出包：{}".format(src))
        shutil.copy2(src, FACTORY_DIR / name)
    for need in ["depends-data"]:
        if not (MAIBOT / need).exists():
            raise FileNotFoundError("数据目录缺失，禁止出包：{}".format(MAIBOT / need))
    log(step, "出厂配置副本 + 模板/depends-data 完整性 OK → {}".format(FACTORY_DIR))


def write_manifest_and_diff(step):
    """步骤 7：site-packages 清单落盘，并与上次构建 diff（验收：连续两次构建清单 diff 为空）。"""
    dst = RUNTIME / "Lib" / "site-packages"
    lines = sorted(str(p.relative_to(dst)).replace("\\", "/").lower()
                   for p in dst.rglob("*"))
    manifest = MANIFESTS / "site_packages_manifest.txt"
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    if manifest.exists():
        old = set(manifest.read_text(encoding="utf-8").splitlines())
        new = set(lines)
        added, removed = new - old, old - new
        if not added and not removed:
            log(step, "清单 diff 为空 ✓（可重复构建验收通过，共 {} 项）".format(len(lines)))
        else:
            log(step, "清单有变化：+{} / -{}（首次锁版后不应再漂移！）".format(len(added), len(removed)))
            for x in sorted(added)[:10]:
                print("    + {}".format(x))
            for x in sorted(removed)[:10]:
                print("    - {}".format(x))
    else:
        log(step, "首次清单落盘（{} 项）".format(len(lines)))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify(step):
    """步骤 8：验收自检（runtime 自含：site-packages + pip + 源码路径）。"""
    py = RUNTIME / "python.exe"
    if not py.exists():
        raise FileNotFoundError("runtime\\python.exe 不存在")
    run([py, "-c", "import {}".format(", ".join(VERIFY_IMPORTS))], timeout=300)
    log(step, "验收硬指标 import fastapi, faiss, igraph + 大件巡检 ✓")

    run([py, "-m", "pip", "--version"], capture_output=True)
    log(step, "pip 内置可用（E1 第 4 条）✓")

    run([py, "-m", "py_compile", str(MAIBOT / "bot.py"),
         str(MAIBOT / "config_manager.py"), str(MAIBOT / "init_napcat.py")])
    log(step, "bot.py / config_manager.py / init_napcat.py 语法过审（py_compile 铁律）✓")

    # dist 布局预演：._pth 的 ..\MaiBot 需要 runtime 的兄弟目录，零拷贝 junction 指向真源码树
    if not JUNCTION_MAIBOT.exists():
        import _winapi
        _winapi.CreateJunction(str(MAIBOT), str(JUNCTION_MAIBOT))
        log(step, "dist\\MaiBot junction 已建（构建机验证布局用，非分发物）")
    # 轻量探针：不碰 src.main（其 import 链会模块级连 MongoDB，构建机无 mongod 必 30s 超时——
    # 全链路跑通属 E1 验收"真机 bot.py 直跑"，归老大真机）。event_bus+config 足以证明
    # ._pth（..\MaiBot）+ site-packages + src 解析三件全通。
    out = subprocess.run(
        [str(py), "-c", "import src.common.event_bus; import src.config.config; print('src OK')"],
        cwd=str(DIST), capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        print(out.stdout or "")
        print(out.stderr or "")
        raise RuntimeError("runtime 下 import src 探针失败（._pth 的 ..\\MaiBot 未生效？）")
    log(step, "..\\MaiBot 源码路径生效（event_bus + config 探针 OK）✓")


def main():
    ap = argparse.ArgumentParser(description="墨忆MOUI-MAX E1 runtime 构建器")
    ap.add_argument("--index-url", default=DEFAULT_INDEX, help="PyPI 镜像（默认清华）")
    ap.add_argument("--skip-bootstrap", action="store_true", help="跳过构建机 Python 自举（已就绪时）")
    ap.add_argument("--only-verify", action="store_true", help="只跑步骤 8 验收自检")
    args = ap.parse_args()

    t0 = time.time()
    log(0, "墨忆MOUI-MAX runtime 构建开始 · VERSION={}".format(
        VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "缺失!"))
    if not VERSION_FILE.exists():
        raise FileNotFoundError("仓库根 VERSION 缺失（四审③：施工第一步）")

    if args.only_verify:
        verify(8)
    else:
        if not args.skip_bootstrap:
            bootstrap_build_python(1)
        else:
            log(1, "按参数跳过自举")
        stage_embeddable(2)
        req = filter_requirements(3)
        install_deps(4, PY312_DIR / "python.exe", req, args.index_url)
        merge_site_packages(5, None)
        emit_factory_config(6)
        write_manifest_and_diff(7)
        verify(8)

    log(0, "构建完成，用时 {:.0f}s。产物：{}".format(time.time() - t0, RUNTIME))
    log(0, "E1 验收指引：干净虚拟机（无 Python）装包后直接 runtime\\python.exe bot.py 跑通。")


if __name__ == "__main__":
    main()
