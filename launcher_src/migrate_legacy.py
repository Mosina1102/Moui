#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨忆MOUI-MAX · 旧绿色一键包数据导入器（exe化策划书 v2.4 §四 E2 第 9 条）
=========================================================================
归属：清单与校验归 E2（本文件），目录选择交互归 E3 首启流程（调 migrate()）。

双布局自动探测（E0 ④：部署机为 MotricSeven 布局，一键包为另一布局）：
  布局A 一键包式： <source>\\MaiBot\\mongodb\\db 存在          → 旧根 = <source>\\MaiBot
  布局B 郭特瑞克式：<source>\\mongodb\\db 存在（无 MaiBot 子层） → 旧根 = <source>

搬运清单（存量用户无痛升舱的生死线，三审⑤）：
  1. mongodb\\db\\           → 数据库（群聊记录/人物卡/好感度真身）
  2. config\\bot_config.toml / lpmm_config.toml
  3. .env                    （API key 等）
  4. data\\                  （图片/语音/人物卡/记忆/每日备份）
  5. napcat\\versions\\*\\resources\\app\\napcat\\config\\   （onebot11_<QQ>.json 等）
  6. MaiBot-Napcat-Adapter\\config.toml

红线（E0 ③）：QQ 登录缓存落 Documents\\Tencent Files（包外天然保留）——
本导入器绝不读写、绝不删除该目录；搬运/校验一律只碰包内路径。
"""
import argparse
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# ---------------- 常量区 ----------------
MONGOD_PORT = 27017          # mongod 运行中 = 数据库文件被锁，搬运必炸，前置拦截
DB_DIR = Path("mongodb") / "db"
REL_ITEMS = [                # (相对旧根路径, 相对新 MaiBot 根路径, 是否目录)
    (DB_DIR, DB_DIR, True),                                # 数据库
    (Path("config") / "bot_config.toml", Path("config") / "bot_config.toml", False),
    (Path("config") / "lpmm_config.toml", Path("config") / "lpmm_config.toml", False),
    (Path(".env"), Path(".env"), False),
    (Path("data"), Path("data"), True),                    # 图片/语音/记忆/备份
    (Path("MaiBot-Napcat-Adapter") / "config.toml",
     Path("MaiBot-Napcat-Adapter") / "config.toml", False),
]
NAPCAT_CONFIG_GLOB = Path("napcat") / "versions"


def detect_layout(source: Path):
    """返回旧根目录（MaiBot 子层或 source 本身）；探不出返回 None（附原因）。"""
    a = source / "MaiBot" / DB_DIR
    b = source / DB_DIR
    if a.exists():
        return source / "MaiBot", "布局A·一键包式（MaiBot 子层）"
    if b.exists():
        return source, "布局B·MotricSeven式（无 MaiBot 子层）"
    return None, ("未探出已知布局：既没有 {} 也没有 {}".format(a, b))


def mongod_running() -> bool:
    """双保险：27017 可连 或 系统里有 mongod.exe 进程 → 数据库在跑。"""
    try:
        with socket.create_connection(("127.0.0.1", MONGOD_PORT), timeout=1):
            return True
    except OSError:
        pass
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mongod.exe"],
                             capture_output=True, text=True, timeout=15).stdout or ""
        return "mongod.exe" in out
    except Exception:
        return False   # 探测失败放行（后续复制出错会兜底报错）


def copy_item(src: Path, dst: Path, is_dir: bool) -> str:
    """单项搬运（fail-open 由调用方 try/except 收集）。"""
    if not src.exists():
        return "跳过（旧包中不存在）：{}".format(src)
    if is_dir:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return ""


def migrate(source_str: str, dest_str: str, progress=None) -> (bool, list):
    """
    执行全量导入。返回 (成功与否, 报告行列表)。
    progress(cur, total, name) 回调供 E3 向导渲染进度条；传 None 则静默。
    """
    if progress is None:
        progress = lambda *a, **k: None
    source = Path(source_str).resolve()
    dest_root = Path(dest_str).resolve()   # 新安装根 {localappdata}\Moui
    dest_mai = dest_root / "MaiBot"
    report = []

    if not source.exists():
        return False, ["[错误] 旧包目录不存在：{}".format(source)]

    old_root, why = detect_layout(source)
    if old_root is None:
        return False, ["[错误] " + why]
    report.append("布局探测：{} → {}".format(why, old_root))

    if mongod_running():
        return False, ["[错误] 检测到 MongoDB 正在运行（27017 端口/mongod 进程），"
                       "数据库文件被锁时搬运会损坏数据——请先彻底停止 Moui/旧包服务再导入。"]

    dest_mai.mkdir(parents=True, exist_ok=True)

    # 固定清单 + napcat 配置通配（versions\<任意QQ版本>\...\napcat\config）
    jobs = []
    for rel_src, rel_dst, is_dir in REL_ITEMS:
        jobs.append((old_root / rel_src, dest_mai / rel_dst, is_dir, str(rel_src)))
    nap_cfg_root = old_root / NAPCAT_CONFIG_GLOB
    if nap_cfg_root.exists():
        for cfg in sorted(nap_cfg_root.glob("*\\resources\\app\\napcat\\config")):
            tail = cfg.relative_to(old_root)
            jobs.append((cfg, dest_mai / tail, True, str(tail)))

    total = len(jobs)
    errors = []
    for i, (src, dst, is_dir, name) in enumerate(jobs, 1):
        progress(i, total, name)
        try:
            msg = copy_item(src, dst, is_dir)
            if msg:
                report.append("  " + msg)
            else:
                report.append("  搬运完成：{}".format(name))
        except Exception as exc:   # fail-open：单项失败不中断整包
            errors.append("{}：{}".format(name, exc))
            report.append("  [失败] {}：{}".format(name, exc))

    # ---- 校验（三审⑤：校验归 E2） ----
    db_dst = dest_mai / DB_DIR
    wt_count = len(list(db_dst.glob("*.wt"))) if db_dst.exists() else 0
    report.append("校验：mongodb\\db 内 WiredTiger 文件 {} 个{}".format(
        wt_count, "（非零=数据库真身到位）" if wt_count else "（警告：为空，旧库可能是全新包）"))
    for must in [dest_mai / "config" / "bot_config.toml", dest_mai / ".env"]:
        if must.exists():
            report.append("校验：{} 到位".format(must.name if must.parent == dest_mai else must))
        else:
            report.append("校验：[警告] {} 缺失（首启向导会重新生成，但 API key/人设需重配）".format(must))

    if errors:
        report.insert(0, "导入完成，但 {} 项失败：".format(len(errors)))
        return False, report
    report.insert(0, "导入完成，全部成功（{} 项）。".format(total))
    return True, report


def main():
    ap = argparse.ArgumentParser(description="旧绿色一键包 → 墨忆MOUI-MAX 数据导入器")
    ap.add_argument("--source", required=True, help="旧包目录（一键包根 或 MotricSeven 根）")
    ap.add_argument("--dest", required=True, help="新安装根（默认 {localappdata}\\Moui）")
    args = ap.parse_args()
    ok, report = migrate(args.source, args.dest)
    for line in report:
        print(line)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
