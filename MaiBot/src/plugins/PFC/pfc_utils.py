import json
import re
from typing import Dict, Any, Optional, Tuple, List, Union
from src.common.logger import get_module_logger

logger = get_module_logger("pfc_utils")


def strip_code_fences(text: str) -> str:
    """剥离 markdown 代码围栏（```json ... ```），LLM爱用围栏包JSON"""
    return re.sub(r"```[a-zA-Z]*", "", text).strip()


def extract_first_json_span(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    """字符串感知的平衡括号扫描，取出第一个完整的JSON片段。

    比弱正则 \\{[^{}]*\\} 强在：容忍嵌套括号、容忍reason里的花括号、容忍前后缀杂讯。
    """
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def loads_with_newline_repair(span: str) -> Optional[Any]:
    """常规解析失败时，把字符串里的原始换行转义后重试。

    GLM系喜欢在 reason 里直接塞真实换行，那在JSON里是非法的，转义一下就能救回来。
    """
    if not span:
        return None
    try:
        return json.loads(span)
    except json.JSONDecodeError:
        pass
    repaired = span.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def get_items_from_json(
    content: str,
    private_name: str,
    *items: str,
    default_values: Optional[Dict[str, Any]] = None,
    required_types: Optional[Dict[str, type]] = None,
    allow_array: bool = True,
    allow_empty: bool = False,
) -> Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]:
    """从文本中提取JSON内容并获取指定字段

    Args:
        content: 包含JSON的文本
        *items: 要提取的字段名
        default_values: 字段的默认值，格式为 {字段名: 默认值}
        required_types: 字段的必需类型，格式为 {字段名: 类型}
        allow_array: 是否允许解析JSON数组

    Returns:
        Tuple[bool, Union[Dict[str, Any], List[Dict[str, Any]]]]: (是否成功, 提取的字段字典或字典列表)
    """
    content = strip_code_fences(content.strip())
    result = {}

    # 设置默认值
    if default_values:
        result.update(default_values)

    # 首先尝试解析为JSON数组
    if allow_array:
        json_array = None
        # 优先用平衡括号扫描提取（容忍嵌套与前后缀杂讯）
        array_span = extract_first_json_span(content, "[", "]")
        if array_span:
            json_array = loads_with_newline_repair(array_span)
        if not isinstance(json_array, list):
            # 兼容原有贪婪正则路径
            array_match = re.search(r"\[[\s\S]*\]", content)
            if array_match:
                json_array = loads_with_newline_repair(array_match.group())

        if isinstance(json_array, list):
            # 验证数组中的每个项目是否包含所有必需字段
            valid_items = []
            for item in json_array:
                if not isinstance(item, dict):
                    continue

                # 检查是否有所有必需字段
                if all(field in item for field in items):
                    # 验证字段类型
                    if required_types:
                        type_valid = True
                        for field, expected_type in required_types.items():
                            if field in item and not isinstance(item[field], expected_type):
                                type_valid = False
                                break

                        if not type_valid:
                            continue

                    # 验证字符串字段不为空
                    string_valid = True
                    for field in items:
                        if isinstance(item[field], str) and not item[field].strip():
                            string_valid = False
                            break

                    if not string_valid:
                        continue

                    valid_items.append(item)

            if valid_items:
                return True, valid_items

        logger.debug(f"[私聊][{private_name}]JSON数组解析失败，尝试解析单个JSON对象")

    # 尝试解析JSON对象：直接解析 → 平衡扫描提取{...} → 原始换行转义修复
    json_data = loads_with_newline_repair(content)
    if not isinstance(json_data, dict):
        object_span = extract_first_json_span(content, "{", "}")
        json_data = loads_with_newline_repair(object_span) if object_span else None

    if not isinstance(json_data, dict):
        logger.error(f"[私聊][{private_name}]提取的JSON内容解析失败，原文前200字: {content[:200]}")
        return False, result

    # 提取字段
    for item in items:
        if item in json_data:
            result[item] = json_data[item]

    # 验证必需字段
    if not all(item in result for item in items):
        logger.error(f"[私聊][{private_name}]JSON缺少必要字段，实际内容: {json_data}")
        return False, result

    # 验证字段类型
    if required_types:
        for field, expected_type in required_types.items():
            if field in result and not isinstance(result[field], expected_type):
                logger.error(f"[私聊][{private_name}]{field} 必须是 {expected_type.__name__} 类型")
                return False, result

    # 验证字符串字段不为空（allow_empty=True 时跳过：no_reply/at_qq缺省等场景空串是合法值）
    if not allow_empty:
        for field in items:
            if isinstance(result[field], str) and not result[field].strip():
                logger.error(f"[私聊][{private_name}]{field} 不能为空")
                return False, result

    return True, result
