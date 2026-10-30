from typing import Any, Dict

from .account_action_common import call_napcat_action, check_cooldown, record_action, set_cooldown
from .base_tool import BaseTool


class ChangeNicknameTool(BaseTool):
    name = "change_nickname"
    description = (
        "修改自己的QQ昵称。nickname 控制在24字以内。一天能改一次，"
        "除非真的想换名字，否则不要调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "新的QQ昵称，24字以内"},
        },
        "required": ["nickname"],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        allowed, remain = check_cooldown(self.name)
        if not allowed:
            return {"status": "failed", "message": f"昵称最近刚改过，冷却中（还需 {remain:.1f} 小时），7天内别再改了"}

        nickname = (function_args.get("nickname") or "").strip()
        if not nickname:
            return {"status": "failed", "message": "nickname 不能为空"}
        if len(nickname) > 24:
            return {"status": "failed", "message": f"昵称太长了（{len(nickname)}字），控制在24字以内"}

        result = await call_napcat_action("set_qq_profile", {"nickname": nickname})
        if result.get("status") == "ok":
            set_cooldown(self.name)
            record_action(self.name, f"昵称已更新: {nickname}")
            return {"status": "ok", "message": f"昵称已改为「{nickname}」"}
        return result
