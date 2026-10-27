from src.do_tool.tool_can_use.base_tool import (
    BaseTool,
    register_tool,
    discover_tools,
    get_all_tool_definitions,
    get_tool_instance,
    TOOL_REGISTRY,
)

__all__ = [
    "BaseTool",
    "register_tool",
    "discover_tools",
    "get_all_tool_definitions",
    "get_tool_instance",
    "TOOL_REGISTRY",
]

# 自动发现并注册工具
discover_tools()
