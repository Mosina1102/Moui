# -*- coding: utf-8 -*-
"""墨忆MOUI-MAX 启动器 · 单实例互斥（坑⑩：双击两次=双份心流群里同秒两条话，拟真穿帮事故）"""
import ctypes
import sys

from config_paths import APP_ID

MUTEX_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
INVALID_HANDLE_VALUE = -1


class SingleInstance:
    """CreateMutexW 命名互斥体：第二实例创建失败=已有实例在跑。"""

    def __init__(self):
        self.handle = None
        # use_last_error=True：ctypes 才会可靠快照 GetLastError（否则读到陈旧值误判已存在）
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def acquire(self) -> bool:
        name = ctypes.c_wchar_p("Global\\" + APP_ID)
        self.handle = self._kernel32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
        if self.handle in (None, INVALID_HANDLE_VALUE):
            # 权限受限时退化到 Local\\ 命名空间再试一次
            self.handle = self._kernel32.CreateMutexW(None, False, ctypes.c_wchar_p("Local\\" + APP_ID))
            err = ctypes.get_last_error()
            if self.handle in (None, INVALID_HANDLE_VALUE):
                return True   # 探测不了就放行（fail-open：宁可双开也别起不来）
        if err in (MUTEX_ALREADY_EXISTS, ERROR_ACCESS_DENIED):
            return False
        return True

    def release(self):
        if self.handle not in (None, INVALID_HANDLE_VALUE):
            self._kernel32.ReleaseMutex(self.handle)
            self.handle = None
