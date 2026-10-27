from dataclasses import dataclass
from typing import List


@dataclass
class MemoryConfig:
    """记忆系统配置类"""

    # 记忆构建相关配置
    memory_build_distribution: List[float]  # 记忆构建的时间分布参数
    build_memory_sample_num: int  # 每次构建记忆的样本数量
    build_memory_sample_length: int  # 每个样本的消息长度
    memory_compress_rate: float  # 记忆压缩率

    # 记忆遗忘相关配置
    memory_forget_time: int  # 记忆遗忘时间（小时）

    # 记忆过滤相关配置
    memory_ban_words: List[str]  # 记忆过滤词列表

    # 新增：记忆整合相关配置
    consolidation_similarity_threshold: float  # 相似度阈值
    consolidate_memory_percentage: float  # 检查节点比例
    consolidate_memory_interval: int  # 记忆整合间隔

    llm_topic_judge: str  # 话题判断模型
    llm_summary: str  # 话题总结模型

    @classmethod
    def from_global_config(cls, global_config):
        """从全局配置创建记忆系统配置"""
        # 使用 getattr 提供默认值，防止全局配置缺少这些项
        return cls(
            memory_build_distribution=getattr(
                global_config, "memory_build_distribution", (24, 12, 0.5, 168, 72, 0.5)
            ),  # 添加默认值
            build_memory_sample_num=getattr(global_config, "build_memory_sample_num", 5),
            build_memory_sample_length=getattr(global_config, "build_memory_sample_length", 30),
            memory_compress_rate=getattr(global_config, "memory_compress_rate", 0.1),
            memory_forget_time=getattr(global_config, "memory_forget_time", 24 * 7),
            memory_ban_words=getattr(global_config, "memory_ban_words", []),
            # 新增加载整合配置，并提供默认值
            consolidation_similarity_threshold=getattr(global_config, "consolidation_similarity_threshold", 0.7),
            consolidate_memory_percentage=getattr(global_config, "consolidate_memory_percentage", 0.01),
            consolidate_memory_interval=getattr(global_config, "consolidate_memory_interval", 1000),
            llm_topic_judge=getattr(global_config, "llm_topic_judge", "default_judge_model"),  # 添加默认模型名
            llm_summary=getattr(global_config, "llm_summary", "default_summary_model"),  # 添加默认模型名
        )
