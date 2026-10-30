from typing import Any, Dict

from .account_action_common import call_napcat_action, check_cooldown, record_action, set_cooldown
from .base_tool import BaseTool


class SendPokeTool(BaseTool):
    name = "send_poke"
    description = (
        "戳一戳/拍一拍对方：回应对方的拍一拍（拍回去就是打招呼）、想找对方搭话但不知道说什么、"
        "催促或玩闹。群聊需给 user_id（对方QQ号，从群成员速查表取）；私聊什么都不用填，自动拍当前聊天对象。"
        "每小时只能戳一次，别骚扰别人。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "group_id": {"type": "integer", "description": "群号（群聊可不填，自动从当前会话取）"},
            "user_id": {"type": "integer", "description": "要戳的对方QQ号（私聊可不填，自动取当前聊天对象）"},
        },
        "required": [],
    }

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        allowed, remain = check_cooldown(self.name)
        if not allowed:
            return {"status": "failed", "message": f"刚戳过人，冷却中（还需 {remain * 60:.0f} 分钟），别骚扰群友"}

        # group_id：显式传入 > 从当前会话反查（群聊）> 不传（私聊好友拍）
        group_id = None
        if function_args.get("group_id"):
            try:
                group_id = int(function_args.get("group_id"))
            except (TypeError, ValueError):
                group_id = None

        # user_id：显式传入 > 私聊从当前会话反查对方
        user_id = None
        if function_args.get("user_id"):
            try:
                user_id = int(function_args.get("user_id"))
            except (TypeError, ValueError):
                user_id = None

        chat_stream = None
        chat_id_raw = function_args.get("chat_id")
        if chat_id_raw:
            try:
                from src.plugins.chat.chat_stream import chat_manager

                chat_stream = chat_manager.get_stream(str(chat_id_raw))
            except Exception:
                chat_stream = None

        if group_id is None and chat_stream is not None:
            gid = getattr(getattr(chat_stream, "group_info", None), "group_id", None)
            if gid:
                group_id = int(gid)

        if user_id is None and chat_stream is not None:
            # 私聊：chat_stream.user_info 就是对方（群聊场景该字段不是"目标人"，不可用）
            if group_id is None:
                uid = getattr(getattr(chat_stream, "user_info", None), "user_id", None)
                if uid:
                    user_id = int(uid)

        if user_id is None:
            return {"status": "failed", "message": "缺少要戳的人的QQ号（群聊请从群成员速查表填 user_id）"}

        # 防呆：不戳自己（私聊对象是自己账号的脏聊天流时，自动反查会拿到 BOT_QQ）
        try:
            from ...config.config import global_config as _cfg

            if str(user_id) == str(_cfg.BOT_QQ):
                return {"status": "failed", "message": "不能戳自己"}
        except Exception:
            pass

        params: Dict[str, Any] = {"user_id": user_id}
        if group_id:
            params["group_id"] = group_id

        result = await call_napcat_action("send_poke", params)
        if result.get("status") == "ok":
            set_cooldown(self.name)
            scene = f"群 {group_id}" if group_id else "私聊"
            record_action(self.name, f"戳了 {user_id}（{scene}）")
            return {"status": "ok", "message": "戳一戳已发送"}
        return result
