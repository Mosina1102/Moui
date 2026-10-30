import asyncio
import os
import sys

# 路径自举（exe版 runtime 的 ._pth 会禁用"脚本目录自动进 sys.path"，导致 import src
# 撞上 MaiCore 的同名包而 ModuleNotFoundError——把本文件所在目录插到最前恢复旧行为）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import websockets as Server
from src.logger import logger
from src.recv_handler import recv_handler
from src.send_handler import send_handler
from src.action_server import start_action_server
from src.config import global_config
from src.mmc_com_layer import mmc_start_com, mmc_stop_com, router
from src.message_queue import message_queue, put_response, check_timeout_response


async def message_recv(server_connection: Server.ServerConnection):
    recv_handler.server_connection = server_connection
    send_handler.server_connection = server_connection
    try:
        async for raw_message in server_connection:
            try:
                logger.debug(f"{raw_message[:80]}..." if len(raw_message) > 80 else raw_message)
                decoded_raw_message: dict = json.loads(raw_message)
            except json.JSONDecodeError:
                logger.error("收到无法解析为JSON的消息，已跳过该条（不影响后续消息）")
                continue
            post_type = decoded_raw_message.get("post_type")
            if post_type == "meta_event":
                await message_queue.put(decoded_raw_message)
            elif post_type == "message":
                await message_queue.put(decoded_raw_message)
            elif post_type == "notice":
                await message_queue.put(decoded_raw_message)
            elif post_type is None:
                await put_response(decoded_raw_message)
    except Server.exceptions.ConnectionClosedError as e:
        # 单帧超限/网络中断只断连接，不再向上炸死整个进程；NapCat 会自动重连进来
        logger.warning(f"与NapCat的连接中断（{e}），等待NapCat重新连入...")
    except Exception as e:
        logger.error(f"消息接收循环异常: {e}（连接循环退出，等待NapCat重新连入）")


async def message_process():
    while True:
        message = await message_queue.get()
        post_type = message.get("post_type")
        if post_type == "message":
            await recv_handler.handle_raw_message(message)
        elif post_type == "meta_event":
            await recv_handler.handle_meta_event(message)
        elif post_type == "notice":
            await recv_handler.handle_notice(message)
        else:
            logger.warning(f"未知的post_type: {post_type}")
        message_queue.task_done()
        await asyncio.sleep(0.05)


async def main():
    recv_handler.maibot_router = router
    start_action_server()
    _ = await asyncio.gather(napcat_server(), mmc_start_com(), message_process(), check_timeout_response())


async def napcat_server():
    logger.info("正在启动adapter...")
    # max_size：单帧上限，默认 1MB 会被大图 base64 消息（实测 1.4MB）触发 1009 炸连接，放宽到 50MB
    async with Server.serve(
        message_recv, global_config.server_host, global_config.server_port, max_size=50 * 1024 * 1024
    ) as server:
        logger.info(f"Adapter已启动，监听地址: ws://{global_config.server_host}:{global_config.server_port}")
        await server.serve_forever()


async def graceful_shutdown():
    try:
        logger.info("正在关闭adapter...")
        await mmc_stop_com()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    except Exception as e:
        logger.error(f"Adapter关闭失败: {e}")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.warning("收到中断信号，正在优雅关闭...")
        loop.run_until_complete(graceful_shutdown())
    except Exception as e:
        logger.exception(f"主程序异常: {str(e)}")
        if loop and not loop.is_closed():
            loop.run_until_complete(graceful_shutdown())
            loop.close()
        sys.exit(1)
