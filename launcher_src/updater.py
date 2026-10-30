# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX 启动器 · 热更新（E4 · 全量安装包自更新，MSMate 模式）
====================================================================
流程：version.json 探测（自托管→GitHub 回退，坑⑬）→ min_compatible 兼容性守卫（六审①）→
下载到 %TEMP%\\moui_update（直连→ghproxy 镜像链回退+断点续传，风险 2）→ SHA256 校验
（失败重试一次，仍失败保旧版，四审⑥）→ 优雅停全家桶（坑⑭）→ 分离助手 2 秒自举（坑⑫）。
VERSION=产品版本；bot_config [inner] version=配置 schema 版本——两个数字永不相等。
"""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from config_paths import (
    GH_MIRRORS,
    SETUP_NAME_FMT,
    TEMP_UPDATE_DIR,
    UPDATE_DONE_FLAG,
    UPDATE_URL_FALLBACK,
    UPDATE_URL_PRIMARY,
    VERSION_FILE,
    read_version,
)

DOWNLOAD_CHUNK = 1024 * 512
UPDATE_TIMEOUT = 60 * 30


def _ver_tuple(v: str):
    """'0.6.3' → (0, 6, 3)；脏字符串按数字段提取兜底。"""
    nums = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


def _fetch_json(urls, log=print):
    """多 URL 依序探测，首个成功的 JSON 返回。"""
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MoUIMax-Launcher/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            log("版本源不可用 {}: {}".format(url, exc))
    return None


def check_update(log=print):
    """
    返回 dict：
      {"status": "none"|"available"|"blocked"|"error",
       "version": str, "download_url": str, "sha256": str, "notes": str}
    前置守卫（五审②）由调用方负责：config_is_ready() 为 False 时不应调用本函数。
    """
    local = read_version()

    # 1) 自托管 version.json（moui.mosina.top：version/download/sha256/min_compatible/notes）
    meta = _fetch_json([UPDATE_URL_PRIMARY], log)
    if meta and "version" in meta:
        remote_ver = str(meta["version"])
        min_compatible = str(meta.get("min_compatible", "0.0.0"))
        if _ver_tuple(local) < _ver_tuple(min_compatible):
            # 六审①：MaiCore 无数据迁移机制，跨大版本静默重装=新程序配旧数据必翻车
            return {"status": "blocked", "version": remote_ver, "download_url": "",
                    "sha256": "", "notes": "这是个大版本更新，需要搬家。请到官网查看迁移指南。"}
        if _ver_tuple(remote_ver) <= _ver_tuple(local):
            return {"status": "none", "version": local, "download_url": "", "sha256": "", "notes": ""}
        urls = [meta.get("download_url", "")]
        return {"status": "available", "version": remote_ver,
                "download_url": urls[0], "sha256": str(meta.get("sha256", "")),
                "notes": str(meta.get("notes", ""))}

    # 2) 回退 GitHub API（坑⑬：国内易挂且有匿名限流，只做兜底）
    meta = _fetch_json([UPDATE_URL_FALLBACK], log)
    if not meta:
        return {"status": "error", "version": local, "download_url": "", "sha256": "",
                "notes": "检查更新失败（版本源都不可达）"}
    tag = str(meta.get("tag_name", ""))
    remote_ver = tag.lstrip("vV") or local
    if _ver_tuple(remote_ver) <= _ver_tuple(local):
        return {"status": "none", "version": local, "download_url": "", "sha256": "", "notes": ""}
    for asset in meta.get("assets", []):
        if str(asset.get("name", "")).lower() == SETUP_NAME_FMT.format(ver=remote_ver).lower():
            return {"status": "available", "version": remote_ver,
                    "download_url": str(asset.get("browser_download_url", "")),
                    "sha256": "", "notes": ""}
    return {"status": "none", "version": local, "download_url": "", "sha256": "",
            "notes": "远端 {} 存在但没找到对应安装包资产".format(remote_ver)}


def _download(url: str, dest: Path, log=print, progress=None) -> bool:
    """断点续传下载（Range 头）。镜像链在调用方轮换。"""
    headers = {"User-Agent": "MoUIMax-Launcher/1.0"}
    mode = "wb"
    existing = dest.stat().st_size if dest.exists() else 0
    if existing:
        headers["Range"] = "bytes={}-".format(existing)
        mode = "ab"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, mode) as fh:
            # 服务器不支持 Range 会回 200 全量——此时必须重写而非追加（防损坏）
            if mode == "ab" and getattr(resp, "status", 206) != 206:
                fh.close()
                with open(dest, "wb") as fh2:
                    pass
                mode = "wb"
                existing = 0
            total = int(resp.headers.get("Content-Length") or 0) + (existing if mode == "ab" else 0)
            while True:
                chunk = resp.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                if progress and total:
                    progress(fh.tell(), total)
        return dest.stat().st_size >= (total or dest.stat().st_size)
    except Exception as exc:
        log("下载失败 {}: {}".format(url, exc))
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def download_update(info: dict, log=print, progress=None) -> Path:
    """
    下载 + 镜像链回退 + 断点续传 + SHA256 校验（四审⑥：失败自动重试一次，
    仍失败抛 RuntimeError——绝不静默安装校验不过的包）。
    """
    TEMP_UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMP_UPDATE_DIR / SETUP_NAME_FMT.format(ver=info["version"])
    expected = (info.get("sha256") or "").lower()

    for attempt in (1, 2):   # 校验失败重试一次
        for mirror in GH_MIRRORS:
            url = (mirror + info["download_url"]) if mirror else info["download_url"]
            if not url:
                continue
            if _download(url, dest, log, progress):
                break
        if not dest.exists():
            continue
        if expected and _sha256(dest) != expected:
            log("SHA256 不匹配（第 {} 次），重下".format(attempt))
            dest.unlink(missing_ok=True)
            continue
        return dest
    raise RuntimeError("这次更新没下完（网络不稳），先用着现在的版本，过会儿再试")


def apply_update(setup_path: Path, pm, log=print):
    """
    坑⑫自举写死：优雅停止 → 写完成标记 → 分离助手 2 秒后起 setup → launcher 主动退出。
    正在运行的 launcher.exe 会被覆盖，所以 setup 必须在 %TEMP% 且由独立于 launcher 的进程拉起。
    """
    TEMP_UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    # 分离助手脚本：独立于 launcher 存活（CREATE_NO_WINDOW + DETACHED_PROCESS）
    helper = TEMP_UPDATE_DIR / "moui_update_helper.bat"
    helper.write_text(
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        'start "" "{}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n'.format(setup_path),
        encoding="ascii",
    )
    # 完成标记：新版 launcher 首启见到 → 气泡"更新完成"（三审遗留清账）
    UPDATE_DONE_FLAG.write_text(json.dumps({"from": read_version(), "at": time.time()}), encoding="ascii")
    log("优雅停止全家桶…")
    pm.stop_all()
    # 等停干净（最多 30s），安装器 [Code] 还有杀进程防线兜底
    deadline = time.time() + 30
    while time.time() < deadline and pm._any_running():
        time.sleep(1)
    log("拉起分离助手，启动器即将退出")
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        cwd=str(TEMP_UPDATE_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        if hasattr(subprocess, "DETACHED_PROCESS") else subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )


def consume_update_flag(log=print):
    """launcher 启动时调用：有更新标记 → 返回 ('from 版本','to 版本') 并清除；无标记 None。"""
    try:
        data = json.loads(UPDATE_DONE_FLAG.read_text(encoding="ascii"))
        UPDATE_DONE_FLAG.unlink(missing_ok=True)
        return data.get("from", "?"), read_version()
    except (OSError, ValueError):
        return None
