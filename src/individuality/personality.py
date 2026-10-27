from dataclasses import dataclass
from typing import Dict, List
import json
from pathlib import Path


@dataclass
class Personality:
    """人格特质类"""

    openness: float  # 开放性
    conscientiousness: float  # 尽责性
    extraversion: float  # 外向性
    agreeableness: float  # 宜人性
    neuroticism: float  # 神经质
    bot_nickname: str  # 机器人昵称
    personality_core: str  # 人格核心特点
    personality_sides: List[str]  # 人格侧面描述

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, personality_core: str = "", personality_sides: List[str] = None):
        if personality_sides is None:
            personality_sides = []
        self.personality_core = personality_core
        self.personality_sides = personality_sides

    @classmethod
    def get_instance(cls) -> "Personality":
        """获取Personality单例实例

        Returns:
            Personality: 单例实例
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_big_five_personality(self):
        """初始化大五人格特质"""
        # 构建文件路径
        personality_file = Path("data/personality") / f"{self.bot_nickname}_personality.per"

        # 如果文件存在，读取文件
        if personality_file.exists():
            with open(personality_file, "r", encoding="utf-8") as f:
                personality_data = json.load(f)
                self.openness = personality_data.get("openness", 0.5)
                self.conscientiousness = personality_data.get("conscientiousness", 0.5)
                self.extraversion = personality_data.get("extraversion", 0.5)
                self.agreeableness = personality_data.get("agreeableness", 0.5)
                self.neuroticism = personality_data.get("neuroticism", 0.5)
        else:
            # 如果文件不存在，根据personality_core和personality_core来设置大五人格特质
            if "活泼" in self.personality_core or "开朗" in self.personality_sides:
                self.extraversion = 0.8
                self.neuroticism = 0.2
            else:
                self.extraversion = 0.3
                self.neuroticism = 0.5

            if "认真" in self.personality_core or "负责" in self.personality_sides:
                self.conscientiousness = 0.9
            else:
                self.conscientiousness = 0.5

            if "友善" in self.personality_core or "温柔" in self.personality_sides:
                self.agreeableness = 0.9
            else:
                self.agreeableness = 0.5

            if "创新" in self.personality_core or "开放" in self.personality_sides:
                self.openness = 0.8
            else:
                self.openness = 0.5

    @classmethod
    def initialize(cls, bot_nickname: str, personality_core: str, personality_sides: List[str]) -> "Personality":
        """初始化人格特质

        Args:
            bot_nickname: 机器人昵称
            personality_core: 人格核心特点
            personality_sides: 人格侧面描述

        Returns:
            Personality: 初始化后的人格特质实例
        """
        instance = cls.get_instance()
        instance.bot_nickname = bot_nickname
        instance.personality_core = personality_core
        instance.personality_sides = personality_sides
        instance._init_big_five_personality()
        return instance

    def to_dict(self) -> Dict:
        """将人格特质转换为字典格式"""
        return {
            "openness": self.openness,
            "conscientiousness": self.conscientiousness,
            "extraversion": self.extraversion,
            "agreeableness": self.agreeableness,
            "neuroticism": self.neuroticism,
            "bot_nickname": self.bot_nickname,
            "personality_core": self.personality_core,
            "personality_sides": self.personality_sides,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Personality":
        """从字典创建人格特质实例"""
        instance = cls.get_instance()
        for key, value in data.items():
            setattr(instance, key, value)
        return instance
