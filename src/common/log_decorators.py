import functools
import inspect
from typing import Callable, Any
from .logger import logger, add_custom_style_handler


def use_log_style(
    style_name: str,
    console_format: str,
    console_level: str = "INFO",
    # file_format: Optional[str] = None, # 暂未支持文件输出
    # file_level: str = "DEBUG",
) -> Callable:
    """装饰器：为函数内的日志启用特定的自定义样式。

    Args:
        style_name (str): 自定义样式的唯一名称。
        console_format (str): 控制台输出的格式字符串。
        console_level (str, optional): 控制台日志级别. Defaults to "INFO".
        # file_format (Optional[str], optional): 文件输出格式 (暂未支持). Defaults to None.
        # file_level (str, optional): 文件日志级别 (暂未支持). Defaults to "DEBUG".

    Returns:
        Callable: 返回装饰器本身。
    """

    def decorator(func: Callable) -> Callable:
        # 获取被装饰函数所在的模块名
        module = inspect.getmodule(func)
        if module is None:
            # 如果无法获取模块（例如，在交互式解释器中定义函数），则使用默认名称
            module_name = "unknown_module"
            logger.warning(f"无法确定函数 {func.__name__} 的模块，将使用 '{module_name}'")
        else:
            module_name = module.__name__

        # 在函数首次被调用（或模块加载时）确保自定义处理器已添加
        # 注意：这会在模块加载时执行，而不是每次函数调用时
        # print(f"Setting up custom style '{style_name}' for module '{module_name}' in decorator definition")
        add_custom_style_handler(
            module_name=module_name,
            style_name=style_name,
            console_format=console_format,
            console_level=console_level,
            # file_format=file_format,
            # file_level=file_level,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 创建绑定了模块名和自定义样式标记的 logger 实例
            custom_logger = logger.bind(module=module_name, custom_style=style_name)
            # print(f"Executing {func.__name__} with custom logger for style '{style_name}'")
            # 将自定义 logger 作为第一个参数传递给原函数
            # 注意：这要求被装饰的函数第一个参数用于接收 logger
            try:
                return func(custom_logger, *args, **kwargs)
            except TypeError as e:
                # 捕获可能的类型错误，比如原函数不接受 logger 参数
                logger.error(
                    f"调用 {func.__name__} 时出错：请确保该函数接受一个 logger 实例作为其第一个参数。错误：{e}"
                )
                # 可以选择重新抛出异常或返回特定值
                raise e

        return wrapper

    return decorator


# --- 示例用法 (可以在其他模块中这样使用) ---

# # 假设这是你的模块 my_module.py
# from src.common.log_decorators import use_log_style
# from src.common.logger import get_module_logger, LoguruLogger

# # 获取模块的标准 logger
# standard_logger = get_module_logger(__name__)

# # 定义一个自定义样式
# MY_SPECIAL_STYLE = "special"
# MY_SPECIAL_FORMAT = "<bg yellow><black> SPECIAL [{time:HH:mm:ss}] </black></bg yellow> | <level>{message}</level>"

# @use_log_style(style_name=MY_SPECIAL_STYLE, console_format=MY_SPECIAL_FORMAT)
# def my_function_with_special_logs(custom_logger: LoguruLogger, x: int, y: int):
#     standard_logger.info("这是一条使用标准格式的日志")
#     custom_logger.info(f"开始执行特殊操作，参数: x={x}, y={y}")
#     result = x + y
#     custom_logger.success(f"特殊操作完成，结果: {result}")
#     standard_logger.info("标准格式日志：函数即将结束")
#     return result

# @use_log_style(style_name="another_style", console_format="<cyan>任务:</cyan> {message}")
# def another_task(task_logger: LoguruLogger, task_name: str):
#     standard_logger.debug("准备执行另一个任务")
#     task_logger.info(f"正在处理任务 '{task_name}'")
#     # ... 执行任务 ...
#     task_logger.warning("任务处理中遇到一个警告")
#     standard_logger.info("另一个任务的标准日志")

# if __name__ == "__main__":
#     print("\n--- 调用 my_function_with_special_logs ---")
#     my_function_with_special_logs(10, 5)
#     print("\n--- 调用 another_task ---")
#     another_task("数据清理")
#     print("\n--- 单独使用标准 logger ---")
#     standard_logger.info("这是一条完全独立的标准日志")
