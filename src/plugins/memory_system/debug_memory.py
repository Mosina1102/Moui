# -*- coding: utf-8 -*-
import asyncio
import time
import sys
import os

# 添加项目根目录到系统路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from src.plugins.memory_system.Hippocampus import HippocampusManager
from src.config.config import global_config


async def test_memory_system():
    """测试记忆系统的主要功能"""
    try:
        # 初始化记忆系统
        print("开始初始化记忆系统...")
        hippocampus_manager = HippocampusManager.get_instance()
        hippocampus_manager.initialize(global_config=global_config)
        print("记忆系统初始化完成")

        # 测试记忆构建
        # print("开始测试记忆构建...")
        # await hippocampus_manager.build_memory()
        # print("记忆构建完成")

        # 测试记忆检索
        test_text = "千石可乐在群里聊天"

        # test_text = '''千石可乐：分不清AI的陪伴和人类的陪伴,是这样吗？'''
        print(f"开始测试记忆检索，测试文本: {test_text}\n")
        memories = await hippocampus_manager.get_memory_from_text(
            text=test_text, max_memory_num=3, max_memory_length=2, max_depth=3, fast_retrieval=False
        )

        await asyncio.sleep(1)

        print("检索到的记忆:")
        for topic, memory_items in memories:
            print(f"主题: {topic}")
            print(f"- {memory_items}")

    except Exception as e:
        print(f"测试过程中出现错误: {e}")
        raise


async def main():
    """主函数"""
    try:
        start_time = time.time()
        await test_memory_system()
        end_time = time.time()
        print(f"测试完成，总耗时: {end_time - start_time:.2f} 秒")
    except Exception as e:
        print(f"程序执行出错: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
