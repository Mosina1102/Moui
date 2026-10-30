from maim_message import Router, RouteConfig, TargetConfig
from .config import global_config
from .logger import logger
from .send_handler import send_handler
from .recv_handler import recv_handler
import asyncio

route_config = RouteConfig(
    route_config={
        global_config.platform: TargetConfig(
            url=f"ws://{global_config.mai_host}:{global_config.mai_port}/ws",
            token=None,
        )
    }
)
router = Router(route_config)


async def mmc_start_com():
    """连接MaiBot——带进程级断线重连。

    Router 自带的连接监控依赖 maim_message 内部状态标志，某些版本断开后
    标志不更新/监控不生效会导致永久失联。这里兜底：run() 只要返回或抛异常
    （断线、服务端重启等），就重建 Router 重连，指数退避到 60 秒。
    """
    global router
    retry = 0
    while True:
        try:
            r = Router(route_config)
            r.register_class_handler(send_handler.handle_seg)
            router = r
            recv_handler.maibot_router = r  # 收到消息后转发给 MaiBot 用的引用同步指向新 Router
            retry = 0
            logger.info("正在连接MaiBot")
            await r.run()
            logger.warning("与MaiBot的连接已断开")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"与MaiBot的连接异常: {e}")
        retry += 1
        wait = min(60, 5 * retry)
        logger.warning(f"{wait} 秒后重连MaiBot（第 {retry} 次重连）...")
        await asyncio.sleep(wait)


async def mmc_stop_com():
    await router.stop()
