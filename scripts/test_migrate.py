# -*- coding: utf-8 -*-
"""E2 第 9 条自测：旧包导入器双布局探测 + 搬运 + 校验（临时目录模拟，构建机直接跑）。"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(r"f:\新版麦麦\MaiBot一键包[Mmax]0.6.3\launcher_src")))
import migrate_legacy as m

BASE = Path(tempfile.mkdtemp(prefix="moui_mig_"))


def make_old_package(root: Path, layout: str):
    """layout='A' 一键包式（MaiBot 子层） / 'B' MotricSeven式（无子层）。"""
    mai = root / "MaiBot" if layout == "A" else root
    (mai / "mongodb" / "db").mkdir(parents=True)
    (mai / "mongodb" / "db" / "collection-0--689.wt").write_text("WTDEV")
    (mai / "mongodb" / "db" / "WiredTiger.wat").write_text("WT")
    (mai / "config").mkdir(parents=True)
    (mai / "config" / "bot_config.toml").write_text('qq = "123456"', encoding="utf-8")
    (mai / "config" / "lpmm_config.toml").write_text("# lpmm", encoding="utf-8")
    (mai / ".env").write_text("PORT=8000\nHOST=localhost\nSILICONFLOW_KEY=sk-x", encoding="utf-8")
    (mai / "data").mkdir(parents=True)
    (mai / "data" / "people_card.json").write_text("{}", encoding="utf-8")
    nap_cfg = mai / "napcat" / "versions" / "9.9.9" / "resources" / "app" / "napcat" / "config"
    nap_cfg.mkdir(parents=True)
    (nap_cfg / "onebot11_123456.json").write_text('{"network": {}}', encoding="utf-8")
    (mai / "MaiBot-Napcat-Adapter").mkdir(parents=True)
    (mai / "MaiBot-Napcat-Adapter" / "config.toml").write_text("[onebot]\nhost='localhost'", encoding="utf-8")


def run_case(layout: str):
    src = BASE / ("old_" + layout)
    dst = BASE / ("new_" + layout)
    dst.mkdir(parents=True)
    make_old_package(src, layout)
    ok, report = m.migrate(str(src), str(dst))
    print("---- 布局{} ----".format(layout))
    for line in report:
        print(" ", line)
    mai = dst / "MaiBot"
    checks = [
        (mai / "mongodb" / "db" / "collection-0--689.wt").exists(),
        (mai / "config" / "bot_config.toml").exists(),
        (mai / "config" / "lpmm_config.toml").exists(),
        (mai / ".env").exists(),
        (mai / "data" / "people_card.json").exists(),
        (mai / "napcat" / "versions" / "9.9.9" / "resources" / "app" / "napcat" / "config" / "onebot11_123456.json").exists(),
        (mai / "MaiBot-Napcat-Adapter" / "config.toml").exists(),
    ]
    assert all(checks), "布局{} 有文件缺失: {}".format(layout, checks)
    assert ok, "布局{} migrate 返回失败".format(layout)
    print("布局{} 全部 7 项校验 PASS".format(layout))


run_case("A")
run_case("B")

# 布局探不出的报错路径
ok, report = m.migrate(str(BASE / "old_A" / "nonexist"), str(BASE / "new_A"))
assert not ok and any("不存在" in r for r in report), "不存在目录应报错"
print("不存在目录报错路径 PASS")
shutil.rmtree(BASE, ignore_errors=True)
print("E2 旧包导入器自测全过（双布局 + 校验 + 报错路径）")
