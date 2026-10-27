import sys
import traceback
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_crash_logger():
    """设置崩溃日志记录器"""
    # 创建logs/crash目录（如果不存在）
    crash_log_dir = Path("logs/crash")
    crash_log_dir.mkdir(parents=True, exist_ok=True)

    # 创建日志记录器
    crash_logger = logging.getLogger("crash_logger")
    crash_logger.setLevel(logging.ERROR)

    # 设置日志格式
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s\n异常类型: %(exc_info)s\n详细信息:\n%(message)s\n-------------------\n"
    )

    # 创建按大小轮转的文件处理器（最大10MB，保留5个备份）
    log_file = crash_log_dir / "crash.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    crash_logger.addHandler(file_handler)

    return crash_logger


def log_crash(exc_type, exc_value, exc_traceback):
    """记录崩溃信息到日志文件"""
    if exc_type is None:
        return

    # 获取崩溃日志记录器
    crash_logger = logging.getLogger("crash_logger")

    # 获取完整的异常堆栈信息
    stack_trace = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))

    # 记录崩溃信息
    crash_logger.error(stack_trace, exc_info=(exc_type, exc_value, exc_traceback))


def install_crash_handler():
    """安装全局异常处理器"""
    # 设置崩溃日志记录器
    setup_crash_logger()

    # 保存原始的异常处理器
    original_hook = sys.excepthook

    def exception_handler(exc_type, exc_value, exc_traceback):
        """全局异常处理器"""
        # 记录崩溃信息
        log_crash(exc_type, exc_value, exc_traceback)

        # 调用原始的异常处理器
        original_hook(exc_type, exc_value, exc_traceback)

    # 设置全局异常处理器
    sys.excepthook = exception_handler
