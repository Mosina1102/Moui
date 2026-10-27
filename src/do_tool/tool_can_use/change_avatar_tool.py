import base64
import os
from typing import Any, Dict

from .account_action_common import ALLOWED_IMAGE_DIRS, call_napcat_action, check_cooldown, record_action, set_cooldown
from .base_tool import BaseTool


class ChangeAvatarTool(BaseTool):
    name = "change_avatar"
    description = (
        "更换自己的QQ头像。image_path 必须是允许目录下的图片文件相对路径（如 emoji/xxx.webp 或 data/images/xxx.png），"
        "其他目录一律拒绝。6小时能换一次，想换心情/换画风的时候可以换。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "图片文件相对路径，必须在允许目录（emoji、data/images）内"},
        },
        "required": ["image_path"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        allowed, remain = check_cooldown(self.name)
        if not allowed:
            return {"status": "failed", "message": f"刚换过头像，还在冷却中（还需 {remain:.1f} 小时），先别换了"}

        image_path = (function_args.get("image_path") or "").strip()
        if not image_path:
            return {"status": "failed", "message": "image_path 不能为空"}

        norm = image_path.replace("\\", "/").lstrip("./")
        if not any(norm == d or norm.startswith(d + "/") for d in ALLOWED_IMAGE_DIRS):
            return {"status": "failed", "message": f"只能使用这些目录下的图片: {', '.join(ALLOWED_IMAGE_DIRS)}"}

        if not os.path.isfile(norm):
            return {"status": "failed", "message": f"文件不存在: {norm}"}

        try:
            with open(norm, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            return {"status": "failed", "message": f"读取图片失败: {e}"}

        result = await call_napcat_action("set_qq_avatar", {"file": f"base64://{encoded}"})
        if result.get("status") == "ok":
            set_cooldown(self.name)
            record_action(self.name, f"头像已更换为 {norm}")
            return {"status": "ok", "message": "头像已更换成功"}
        return result
