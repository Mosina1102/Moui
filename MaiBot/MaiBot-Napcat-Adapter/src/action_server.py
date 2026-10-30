import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .logger import logger
from .send_handler import send_handler

# 账号操作 HTTP 服务：本机监听，供 MaiBot 的账号工具调用 NapCat 扩展接口
# 只绑 127.0.0.1，不做对外暴露；不做鉴权（本机信任环境）
ACTION_SERVER_HOST = "127.0.0.1"
ACTION_SERVER_PORT = 8096

_main_loop = None
_httpd = None


class ActionHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self._json(200, {"status": "ok", "message": "action server alive"})
        elif self.path.startswith("/message_id"):
            # 查询 MaiBot 自造消息id 对应的 QQ 真实消息id（"引用自己"功能用）
            query = parse_qs(urlparse(self.path).query)
            local_id = (query.get("local_id") or [""])[0]
            real_id = send_handler.query_real_message_id(local_id) if local_id else ""
            if real_id:
                self._json(200, {"status": "ok", "real_id": real_id})
            else:
                self._json(404, {"status": "failed", "message": "未找到该消息的映射（可能已过期或消息未发送成功）"})
        else:
            self._json(404, {"status": "failed", "message": "not found"})

    def do_POST(self):
        if self.path != "/action":
            self._json(404, {"status": "failed", "message": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"status": "failed", "message": f"请求体解析失败: {e}"})
            return

        action = payload.get("action")
        params = payload.get("params") or {}
        if not action:
            self._json(400, {"status": "failed", "message": "缺少 action"})
            return
        if send_handler.server_connection is None:
            self._json(503, {"status": "failed", "message": "NapCat 连接未就绪"})
            return

        try:
            future = asyncio.run_coroutine_threadsafe(
                send_handler.send_message_to_napcat(action, params), _main_loop
            )
            response = future.result(timeout=20)
            logger.info(f"账号操作 {action} 完成: {str(response)[:120]}")
            self._json(200, {"status": "ok", "data": response})
        except Exception as e:
            logger.error(f"账号操作 {action} 失败: {e}")
            self._json(500, {"status": "failed", "message": str(e)})

    def log_message(self, fmt, *args):
        logger.debug(f"action_server: {fmt % args}")


def start_action_server() -> None:
    """在主事件循环内调用：起后台线程跑 HTTP 服务"""
    global _main_loop, _httpd
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("action_server 未启动：找不到运行中的事件循环")
        return

    try:
        _httpd = ThreadingHTTPServer((ACTION_SERVER_HOST, ACTION_SERVER_PORT), ActionHandler)
    except OSError as e:
        logger.warning(f"账号操作端口 {ACTION_SERVER_PORT} 启动失败（可能被占用）: {e}")
        return

    thread = threading.Thread(target=_httpd.serve_forever, daemon=True, name="action-server")
    thread.start()
    logger.info(f"账号操作 HTTP 服务已启动: http://{ACTION_SERVER_HOST}:{ACTION_SERVER_PORT}/action")
