# 工具系统使用指南

## 概述

`tool_can_use` 是一个插件式工具系统，允许轻松扩展和注册新工具。每个工具作为独立的文件存在于该目录下，系统会自动发现和注册这些工具。

## 工具结构

每个工具应该继承 `BaseTool` 基类并实现必要的属性和方法：

```python
from src.do_tool.tool_can_use.base_tool import BaseTool, register_tool

class MyNewTool(BaseTool):
    # 工具名称，必须唯一
    name = "my_new_tool"
    
    # 工具描述，告诉LLM这个工具的用途
    description = "这是一个新工具，用于..."
    
    # 工具参数定义，遵循JSONSchema格式
    parameters = {
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "参数1的描述"
            },
            "param2": {
                "type": "integer",
                "description": "参数2的描述"
            }
        },
        "required": ["param1"]  # 必需的参数列表
    }
    
    async def execute(self, function_args, message_txt=""):
        """执行工具逻辑
        
        Args:
            function_args: 工具调用参数
            message_txt: 原始消息文本
            
        Returns:
            Dict: 包含执行结果的字典，必须包含name和content字段
        """
        # 实现工具逻辑
        result = f"工具执行结果: {function_args.get('param1')}"
        
        return {
            "name": self.name,
            "content": result
        }

# 注册工具
register_tool(MyNewTool)
```

## 自动注册机制

工具系统通过以下步骤自动注册工具：

1. 在`__init__.py`中，`discover_tools()`函数会自动遍历当前目录中的所有Python文件
2. 对于每个文件，系统会寻找继承自`BaseTool`的类
3. 这些类会被自动注册到工具注册表中

只要确保在每个工具文件的末尾调用`register_tool(YourToolClass)`，工具就会被自动注册。

## 添加新工具步骤

1. 在`tool_can_use`目录下创建新的Python文件（如`my_new_tool.py`）
2. 导入`BaseTool`和`register_tool`
3. 创建继承自`BaseTool`的工具类
4. 实现必要的属性（`name`, `description`, `parameters`）
5. 实现`execute`方法
6. 使用`register_tool`注册工具

## 与ToolUser整合

`ToolUser`类已经更新为使用这个新的工具系统，它会：

1. 自动获取所有已注册工具的定义
2. 基于工具名称找到对应的工具实例
3. 调用工具的`execute`方法

## 使用示例

```python
from src.do_tool.tool_use import ToolUser

# 创建工具用户
tool_user = ToolUser()

# 使用工具
result = await tool_user.use_tool(message_txt="查询关于Python的知识", sender_name="用户", chat_stream=chat_stream)

# 处理结果
if result["used_tools"]:
    print("工具使用结果:", result["collected_info"])
else:
    print("未使用工具")
``` 