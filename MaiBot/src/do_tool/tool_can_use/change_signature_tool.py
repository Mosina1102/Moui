from typing import Any, Dict

from .account_action_common import call_napcat_action, check_cooldown, record_action, set_cooldown
from .base_tool import BaseTool


class ChangeSignatureTool(BaseTool):
    name = "change_signature"
    description = (
        "修改自己的QQ个性签名。signature 控制在100字以内，建议贴合自己当前心情或最近的话题。"
        "6小时能改一次，深夜emo、被夸开心、有感触的时候都可以更新签名。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "signature": {"type": "string", "description": "新的个性签名内容，100字以内"},
        },
        "required": ["signature"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        allowed, remain = check_cooldown(self.name)
        if not allowed:
            return {"status": "failed", "message": f"签名刚改过，还在冷却中（还需 {remain:.1f} 小时）"}

        signature = (function_args.get("signature") or "").strip()
        if not signature:
            return {"status": "failed", "message": "signature 不能为空"}
        if len(signature) > 100:
            return {"status": "failed", "message": f"签名太长了（{len(signature)}字），控制在100字以内"}

        result = await call_napcat_action("set_self_longnick", {"longNick": signature})
        if result.get("status") == "ok":
            set_cooldown(self.name)
            record_action(self.name, f"签名已更新: {signature[:50]}")
            return {"status": "ok", "message": "个性签名已更新"}
        return result
