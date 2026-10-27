from dataclasses import dataclass
from typing import List


@dataclass
class Identity:
    """身份特征类"""

    identity_detail: List[str]  # 身份细节描述
    height: int  # 身高（厘米）
    weight: int  # 体重（千克）
    age: int  # 年龄
    gender: str  # 性别
    appearance: str  # 外貌特征

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        identity_detail: List[str] = None,
        height: int = 0,
        weight: int = 0,
        age: int = 0,
        gender: str = "",
        appearance: str = "",
    ):
        """初始化身份特征

        Args:
            identity_detail: 身份细节描述列表
            height: 身高（厘米）
            weight: 体重（千克）
            age: 年龄
            gender: 性别
            appearance: 外貌特征
        """
        if identity_detail is None:
            identity_detail = []
        self.identity_detail = identity_detail
        self.height = height
        self.weight = weight
        self.age = age
        self.gender = gender
        self.appearance = appearance

    @classmethod
    def get_instance(cls) -> "Identity":
        """获取Identity单例实例

        Returns:
            Identity: 单例实例
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def initialize(
        cls, identity_detail: List[str], height: int, weight: int, age: int, gender: str, appearance: str
    ) -> "Identity":
        """初始化身份特征

        Args:
            identity_detail: 身份细节描述列表
            height: 身高（厘米）
            weight: 体重（千克）
            age: 年龄
            gender: 性别
            appearance: 外貌特征

        Returns:
            Identity: 初始化后的身份特征实例
        """
        instance = cls.get_instance()
        instance.identity_detail = identity_detail
        instance.height = height
        instance.weight = weight
        instance.age = age
        instance.gender = gender
        instance.appearance = appearance
        return instance

    def to_dict(self) -> dict:
        """将身份特征转换为字典格式"""
        return {
            "identity_detail": self.identity_detail,
            "height": self.height,
            "weight": self.weight,
            "age": self.age,
            "gender": self.gender,
            "appearance": self.appearance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Identity":
        """从字典创建身份特征实例"""
        instance = cls.get_instance()
        for key, value in data.items():
            setattr(instance, key, value)
        return instance
