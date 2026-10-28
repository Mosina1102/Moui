"""零依赖测试跑器（pytest 不可用时的兜底）：发现同目录 test_*.py，逐个执行 test_* 函数。

用法：py -3 tests/run_tests.py
有 pytest 的环境仍推荐：py -3 -m pytest tests/ -q
"""
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    passed = failed = 0
    failures = []
    # 注意：不能用 glob——本项目路径含 [Mmax] 会被 glob 当字符类吞掉
    test_files = sorted(
        os.path.join(HERE, n) for n in os.listdir(HERE) if n.startswith("test_") and n.endswith(".py")
    )
    for path in test_files:
        mod_name = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  PASS {mod_name}::{name}")
            except Exception:
                failed += 1
                failures.append((f"{mod_name}::{name}", traceback.format_exc()))
                print(f"  FAIL {mod_name}::{name}")
    for name, tb in failures:
        print(f"\n===== 失败详情: {name} =====\n{tb}")
    print(f"\n结果: {passed} 通过, {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
