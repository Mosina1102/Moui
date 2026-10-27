import numpy as np
from scipy import stats
from datetime import datetime, timedelta


class DistributionVisualizer:
    def __init__(self, mean=0, std=1, skewness=0, sample_size=10):
        """
        初始化分布可视化器

        参数:
            mean (float): 期望均值
            std (float): 标准差
            skewness (float): 偏度
            sample_size (int): 样本大小
        """
        self.mean = mean
        self.std = std
        self.skewness = skewness
        self.sample_size = sample_size
        self.samples = None

    def generate_samples(self):
        """生成具有指定参数的样本"""
        if self.skewness == 0:
            # 对于无偏度的情况，直接使用正态分布
            self.samples = np.random.normal(loc=self.mean, scale=self.std, size=self.sample_size)
        else:
            # 使用 scipy.stats 生成具有偏度的分布
            self.samples = stats.skewnorm.rvs(a=self.skewness, loc=self.mean, scale=self.std, size=self.sample_size)

    def get_weighted_samples(self):
        """获取加权后的样本数列"""
        if self.samples is None:
            self.generate_samples()
        # 将样本值乘以样本大小
        return self.samples * self.sample_size

    def get_statistics(self):
        """获取分布的统计信息"""
        if self.samples is None:
            self.generate_samples()

        return {"均值": np.mean(self.samples), "标准差": np.std(self.samples), "实际偏度": stats.skew(self.samples)}


class MemoryBuildScheduler:
    def __init__(self, n_hours1, std_hours1, weight1, n_hours2, std_hours2, weight2, total_samples=50):
        """
        初始化记忆构建调度器

        参数:
            n_hours1 (float): 第一个分布的均值（距离现在的小时数）
            std_hours1 (float): 第一个分布的标准差（小时）
            weight1 (float): 第一个分布的权重
            n_hours2 (float): 第二个分布的均值（距离现在的小时数）
            std_hours2 (float): 第二个分布的标准差（小时）
            weight2 (float): 第二个分布的权重
            total_samples (int): 要生成的总时间点数量
        """
        # 验证参数
        if total_samples <= 0:
            raise ValueError("total_samples 必须大于0")
        if weight1 < 0 or weight2 < 0:
            raise ValueError("权重必须为非负数")
        if std_hours1 < 0 or std_hours2 < 0:
            raise ValueError("标准差必须为非负数")

        # 归一化权重
        total_weight = weight1 + weight2
        if total_weight == 0:
            raise ValueError("权重总和不能为0")
        self.weight1 = weight1 / total_weight
        self.weight2 = weight2 / total_weight

        self.n_hours1 = n_hours1
        self.std_hours1 = std_hours1
        self.n_hours2 = n_hours2
        self.std_hours2 = std_hours2
        self.total_samples = total_samples
        self.base_time = datetime.now()

    def generate_time_samples(self):
        """生成混合分布的时间采样点"""
        # 根据权重计算每个分布的样本数
        samples1 = max(1, int(self.total_samples * self.weight1))
        samples2 = max(1, self.total_samples - samples1)  # 确保 samples2 至少为1

        # 生成两个正态分布的小时偏移
        hours_offset1 = np.random.normal(loc=self.n_hours1, scale=self.std_hours1, size=samples1)
        hours_offset2 = np.random.normal(loc=self.n_hours2, scale=self.std_hours2, size=samples2)

        # 合并两个分布的偏移
        hours_offset = np.concatenate([hours_offset1, hours_offset2])

        # 将偏移转换为实际时间戳（使用绝对值确保时间点在过去）
        timestamps = [self.base_time - timedelta(hours=abs(offset)) for offset in hours_offset]

        # 按时间排序（从最早到最近）
        return sorted(timestamps)

    def get_timestamp_array(self):
        """返回时间戳数组"""
        timestamps = self.generate_time_samples()
        return [int(t.timestamp()) for t in timestamps]


def print_time_samples(timestamps, show_distribution=True):
    """打印时间样本和分布信息"""
    print(f"\n生成的{len(timestamps)}个时间点分布：")
    print("序号".ljust(5), "时间戳".ljust(25), "距现在（小时）")
    print("-" * 50)

    now = datetime.now()
    time_diffs = []

    for i, timestamp in enumerate(timestamps, 1):
        hours_diff = (now - timestamp).total_seconds() / 3600
        time_diffs.append(hours_diff)
        print(f"{str(i).ljust(5)} {timestamp.strftime('%Y-%m-%d %H:%M:%S').ljust(25)} {hours_diff:.2f}")

    # 打印统计信息
    print("\n统计信息：")
    print(f"平均时间偏移：{np.mean(time_diffs):.2f}小时")
    print(f"标准差：{np.std(time_diffs):.2f}小时")
    print(f"最早时间：{min(timestamps).strftime('%Y-%m-%d %H:%M:%S')} ({max(time_diffs):.2f}小时前)")
    print(f"最近时间：{max(timestamps).strftime('%Y-%m-%d %H:%M:%S')} ({min(time_diffs):.2f}小时前)")

    if show_distribution:
        # 计算时间分布的直方图
        hist, bins = np.histogram(time_diffs, bins=40)
        print("\n时间分布（每个*代表一个时间点）：")
        for i in range(len(hist)):
            if hist[i] > 0:
                print(f"{bins[i]:6.1f}-{bins[i + 1]:6.1f}小时: {'*' * int(hist[i])}")


# 使用示例
if __name__ == "__main__":
    # 创建一个双峰分布的记忆调度器
    scheduler = MemoryBuildScheduler(
        n_hours1=12,  # 第一个分布均值（12小时前）
        std_hours1=8,  # 第一个分布标准差
        weight1=0.7,  # 第一个分布权重 70%
        n_hours2=36,  # 第二个分布均值（36小时前）
        std_hours2=24,  # 第二个分布标准差
        weight2=0.3,  # 第二个分布权重 30%
        total_samples=50,  # 总共生成50个时间点
    )

    # 生成时间分布
    timestamps = scheduler.generate_time_samples()

    # 打印结果，包含分布可视化
    print_time_samples(timestamps, show_distribution=True)

    # 打印时间戳数组
    timestamp_array = scheduler.get_timestamp_array()
    print("\n时间戳数组（Unix时间戳）：")
    print("[", end="")
    for i, ts in enumerate(timestamp_array):
        if i > 0:
            print(", ", end="")
        print(ts, end="")
    print("]")
