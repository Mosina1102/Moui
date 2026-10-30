"""工具执行期的聊天上下文。

两层机制：
1. ContextVar（async 安全）——同一协程链内 set/get
2. 模块级兜底变量——ContextVar 跨 task 边界会丢（set 与 execute 不在同一上下文快照时），
   set_chat_id 同步写兜底，get_chat_id 优先 ContextVar、空则回落兜底
"""
import contextvars

current_chat_id = contextvars.ContextVar("current_chat_id", default=None)

_last_chat_id = None  # 模块级兜底


def set_chat_id(cid):
    """注入当前聊天 id（ContextVar + 模块级兜底双写）"""
    global _last_chat_id
    if cid is not None:
        _last_chat_id = cid
    current_chat_id.set(cid)


def get_chat_id():
    """读取当前聊天 id（ContextVar 优先，空则回落模块级兜底）"""
    v = current_chat_id.get()
    if v is not None:
        return v
    return _last_chat_id
