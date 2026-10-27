import asyncio
import base64
import hashlib
import os
import random
import time
import traceback
from typing import Optional, Tuple
from PIL import Image
import io
import re

from ...common.database import db
from ...config.config import global_config
from ..chat.utils_image import image_path_to_base64, image_manager
from ..models.utils_model import LLMRequest
from src.common.logger_manager import get_logger


logger = get_logger("emoji")

BASE_DIR = os.path.join("data")
EMOJI_DIR = os.path.join(BASE_DIR, "emoji")  # 表情包存储目录
EMOJI_REGISTED_DIR = os.path.join(BASE_DIR, "emoji_registed")  # 已注册的表情包注册目录
MAX_EMOJI_FOR_PROMPT = 20  # 最大允许的表情包描述数量于图片替换的 prompt 中


"""
还没经过测试，有些地方数据库和内存数据同步可能不完全

"""


class MaiEmoji:
    """定义一个表情包"""

    def __init__(self, full_path: str):
        if not full_path:
            raise ValueError("full_path cannot be empty")
        self.full_path = full_path  # 文件的完整路径 (包括文件名)
        self.path = os.path.dirname(full_path)  # 文件所在的目录路径
        self.filename = os.path.basename(full_path)  # 文件名
        self.embedding = []
        self.hash = ""  # 初始为空，在创建实例时会计算
        self.description = ""
        self.emotion = []
        self.usage_count = 0
        self.last_used_time = time.time()
        self.register_time = time.time()
        self.is_deleted = False  # 标记是否已被删除
        self.format = ""

    async def initialize_hash_format(self):
        """从文件创建表情包实例, 计算哈希值和格式"""
        image_base64 = None
        image_bytes = None
        try:
            # 使用 full_path 检查文件是否存在
            if not os.path.exists(self.full_path):
                logger.error(f"[初始化错误] 表情包文件不存在: {self.full_path}")
                self.is_deleted = True
                return None

            # 使用 full_path 读取文件
            logger.debug(f"[初始化] 正在读取文件: {self.full_path}")
            image_base64 = image_path_to_base64(self.full_path)
            if image_base64 is None:
                logger.error(f"[初始化错误] 无法读取或转换Base64: {self.full_path}")
                self.is_deleted = True
                return None
            logger.debug(f"[初始化] 文件读取成功 (Base64预览: {image_base64[:50]}...)")

            # 计算哈希值
            logger.debug(f"[初始化] 正在解码Base64并计算哈希: {self.filename}")
            image_bytes = base64.b64decode(image_base64)
            self.hash = hashlib.md5(image_bytes).hexdigest()
            logger.debug(f"[初始化] 哈希计算成功: {self.hash}")

            # 获取图片格式
            logger.debug(f"[初始化] 正在使用Pillow获取格式: {self.filename}")
            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    self.format = img.format.lower()
                logger.debug(f"[初始化] 格式获取成功: {self.format}")
            except Exception as pil_error:
                logger.error(f"[初始化错误] Pillow无法处理图片 ({self.filename}): {pil_error}")
                logger.error(traceback.format_exc())
                self.is_deleted = True
                return None

            # 如果所有步骤成功，返回 True
            return True

        except FileNotFoundError:
            logger.error(f"[初始化错误] 文件在处理过程中丢失: {self.full_path}")
            self.is_deleted = True
            return None
        except base64.binascii.Error as b64_error:
            logger.error(f"[初始化错误] Base64解码失败 ({self.filename}): {b64_error}")
            self.is_deleted = True
            return None
        except Exception as e:
            logger.error(f"[初始化错误] 初始化表情包时发生未预期错误 ({self.filename}): {str(e)}")
            logger.error(traceback.format_exc())
            self.is_deleted = True
            return None

    async def register_to_db(self):
        """
        注册表情包
        将表情包对应的文件，从当前路径移动到EMOJI_REGISTED_DIR目录下
        并修改对应的实例属性，然后将表情包信息保存到数据库中
        """
        try:
            # 确保目标目录存在

            # 源路径是当前实例的完整路径 self.full_path
            source_full_path = self.full_path
            # 目标完整路径
            destination_full_path = os.path.join(EMOJI_REGISTED_DIR, self.filename)

            # 检查源文件是否存在
            if not os.path.exists(source_full_path):
                logger.error(f"[错误] 源文件不存在: {source_full_path}")
                return False

            # --- 文件移动 ---
            try:
                # 如果目标文件已存在，先删除 (确保移动成功)
                if os.path.exists(destination_full_path):
                    os.remove(destination_full_path)

                os.rename(source_full_path, destination_full_path)
                logger.debug(f"[移动] 文件从 {source_full_path} 移动到 {destination_full_path}")
                # 更新实例的路径属性为新路径
                self.full_path = destination_full_path
                self.path = EMOJI_REGISTED_DIR
                # self.filename 保持不变
            except Exception as move_error:
                logger.error(f"[错误] 移动文件失败: {str(move_error)}")
                # 如果移动失败，尝试将实例状态恢复？暂时不处理，仅返回失败
                return False

            # --- 数据库操作 ---
            try:
                # 准备数据库记录 for emoji collection
                emoji_record = {
                    "filename": self.filename,
                    "path": self.path,  # 存储目录路径
                    "full_path": self.full_path,  # 存储完整文件路径
                    "embedding": self.embedding,
                    "description": self.description,
                    "emotion": self.emotion,
                    "hash": self.hash,
                    "format": self.format,
                    "timestamp": int(self.register_time),
                    "usage_count": self.usage_count,
                    "last_used_time": self.last_used_time,
                }
                
                # 确保full_path字段不为空
                if not self.full_path:
                    # 尝试从旧版本路径构造完整路径
                    if self.filename and self.path:
                        self.full_path = os.path.join(self.path, self.filename)
                        logger.info(f"[兼容模式] 自动补全旧版本表情包路径: {self.full_path}")
                    else:
                        logger.error("[错误] 无法补全表情包路径，缺少必要字段")
                        return False

                # 使用upsert确保记录存在或被更新
                db["emoji"].update_one({"hash": self.hash}, {"$set": emoji_record}, upsert=True)

                logger.success(f"[注册] 表情包信息保存到数据库: {self.filename} ({self.emotion})")

                return True

            except Exception as db_error:
                logger.error(f"[错误] 保存数据库失败 ({self.filename}): {str(db_error)}")
                # 数据库保存失败，是否需要将文件移回？为了简化，暂时只记录错误
                # 可以考虑在这里尝试删除已移动的文件，避免残留
                try:
                    if os.path.exists(self.full_path):  # full_path 此时是目标路径
                        os.remove(self.full_path)
                        logger.warning(f"[回滚] 已删除移动失败后残留的文件: {self.full_path}")
                except Exception as remove_error:
                    logger.error(f"[错误] 回滚删除文件失败: {remove_error}")
                return False

        except Exception as e:
            logger.error(f"[错误] 注册表情包失败 ({self.filename}): {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def delete(self):
        """删除表情包

        删除表情包的文件和数据库记录

        返回:
            bool: 是否成功删除
        """
        try:
            # 1. 删除文件
            file_to_delete = self.full_path
            if os.path.exists(file_to_delete):
                try:
                    os.remove(file_to_delete)
                    logger.debug(f"[删除] 文件: {file_to_delete}")
                except Exception as e:
                    logger.error(f"[错误] 删除文件失败 {file_to_delete}: {str(e)}")
                    # 文件删除失败，但仍然尝试删除数据库记录

            # 2. 删除数据库记录
            result = db.emoji.delete_one({"hash": self.hash})
            deleted_in_db = result.deleted_count > 0

            if deleted_in_db:
                logger.info(f"[删除] 表情包数据库记录 {self.filename} (Hash: {self.hash})")
                # 3. 标记对象已被删除
                self.is_deleted = True
                return True
            else:
                # 如果数据库记录删除失败，但文件可能已删除，记录一个警告
                if not os.path.exists(file_to_delete):
                    logger.warning(
                        f"[警告] 表情包文件 {file_to_delete} 已删除，但数据库记录删除失败 (Hash: {self.hash})"
                    )
                else:
                    logger.error(f"[错误] 删除表情包数据库记录失败: {self.hash}")
                return False

        except Exception as e:
            logger.error(f"[错误] 删除表情包失败 ({self.filename}): {str(e)}")
            return False


class EmojiManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        self._scan_task = None
        self.vlm = LLMRequest(model=global_config.vlm, temperature=0.3, max_tokens=1000, request_type="emoji")
        self.llm_emotion_judge = LLMRequest(
            model=global_config.llm_normal, max_tokens=600, request_type="emoji"
        )  # 更高的温度，更少的token（后续可以根据情绪来调整温度）

        self.emoji_num = 0
        self.emoji_num_max = global_config.max_emoji_num
        self.emoji_num_max_reach_deletion = global_config.max_reach_deletion
        self.emoji_objects: list[MaiEmoji] = []  # 存储MaiEmoji对象的列表，使用类型注解明确列表元素类型
        self._register_fail_counts: dict[str, int] = {}  # 注册失败计数：临时故障保留重试，连续3轮才删（防误杀也防队头阻塞）

        logger.info("启动表情包管理器")

    def _ensure_emoji_dir(self):
        """确保表情存储目录存在"""
        os.makedirs(EMOJI_DIR, exist_ok=True)
        os.makedirs(EMOJI_REGISTED_DIR, exist_ok=True)

    def initialize(self):
        """初始化数据库连接和表情目录"""
        if not self._initialized:
            try:
                self._ensure_emoji_collection()
                self._ensure_emoji_dir()
                self._initialized = True
                # 更新表情包数量
                # 启动时执行一次完整性检查
                # await self.check_emoji_file_integrity()
            except Exception:
                logger.exception("初始化表情管理器失败")

    def _ensure_db(self):
        """确保数据库已初始化"""
        if not self._initialized:
            self.initialize()
        if not self._initialized:
            raise RuntimeError("EmojiManager not initialized")

    @staticmethod
    def _ensure_emoji_collection():
        """确保emoji集合存在并创建索引

        这个函数用于确保MongoDB数据库中存在emoji集合,并创建必要的索引。

        索引的作用是加快数据库查询速度:
        - embedding字段的2dsphere索引: 用于加速向量相似度搜索,帮助快速找到相似的表情包
        - tags字段的普通索引: 加快按标签搜索表情包的速度
        - filename字段的唯一索引: 确保文件名不重复,同时加快按文件名查找的速度

        没有索引的话,数据库每次查询都需要扫描全部数据,建立索引后可以大大提高查询效率。
        """
        if "emoji" not in db.list_collection_names():
            db.create_collection("emoji")
            db.emoji.create_index([("embedding", "2dsphere")])
            db.emoji.create_index([("filename", 1)], unique=True)

    def record_usage(self, hash: str):
        """记录表情使用次数"""
        try:
            db.emoji.update_one({"hash": hash}, {"$inc": {"usage_count": 1}})
            for emoji in self.emoji_objects:
                if emoji.hash == hash:
                    emoji.usage_count += 1
                    break

        except Exception as e:
            logger.error(f"记录表情使用失败: {str(e)}")

    async def get_emoji_for_text(self, text_emotion: str) -> Optional[Tuple[str, str]]:
        """根据文本内容获取相关表情包
        Args:
            text_emotion: 输入的情感描述文本
        Returns:
            Optional[Tuple[str, str]]: (表情包完整文件路径, 表情包描述)，如果没有找到则返回None
        """
        try:
            self._ensure_db()
            _time_start = time.time()

            # 获取所有表情包 (从内存缓存中获取)
            all_emojis = self.emoji_objects

            if not all_emojis:
                logger.warning("内存中没有任何表情包对象")
                # 可以考虑再查一次数据库？或者依赖定期任务更新
                return None

            # 计算每个表情包与输入文本的最大情感相似度
            emoji_similarities = []
            for emoji in all_emojis:
                # 跳过已标记为删除的对象
                if emoji.is_deleted:
                    continue

                emotions = emoji.emotion
                if not emotions:
                    continue

                # 计算与每个emotion标签的相似度，取最大值
                max_similarity = 0
                best_matching_emotion = ""  # 记录最匹配的 emotion 喵~
                for emotion in emotions:
                    # 使用编辑距离计算相似度
                    distance = self._levenshtein_distance(text_emotion, emotion)
                    max_len = max(len(text_emotion), len(emotion))
                    similarity = 1 - (distance / max_len if max_len > 0 else 0)
                    if similarity > max_similarity:  # 如果找到更相似的喵~
                        max_similarity = similarity
                        best_matching_emotion = emotion  # 就记下这个 emotion 喵~

                if best_matching_emotion:  # 确保有匹配的情感才添加喵~
                    emoji_similarities.append((emoji, max_similarity, best_matching_emotion))  # 把 emotion 也存起来喵~

            # 按相似度降序排序
            emoji_similarities.sort(key=lambda x: x[1], reverse=True)

            # 获取前10个最相似的表情包
            top_emojis = (
                emoji_similarities[:10] if len(emoji_similarities) > 10 else emoji_similarities
            )  # 改个名字，更清晰喵~

            if not top_emojis:
                logger.warning("未找到匹配的表情包")
                return None

            # 从前几个中随机选择一个
            selected_emoji, similarity, matched_emotion = random.choice(top_emojis)  # 把匹配的 emotion 也拿出来喵~

            # 更新使用次数
            self.record_usage(selected_emoji.hash)

            _time_end = time.time()

            logger.info(  # 使用匹配到的 emotion 记录日志喵~
                f"为[{text_emotion}]找到表情包: {matched_emotion} ({selected_emoji.filename}), Similarity: {similarity:.4f}"
            )
            # 返回完整文件路径和描述
            return selected_emoji.full_path, f"[ {selected_emoji.description} ]"

        except Exception as e:
            logger.error(f"[错误] 获取表情包失败: {str(e)}")
            return None

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """计算两个字符串的编辑距离

        Args:
            s1: 第一个字符串
            s2: 第二个字符串

        Returns:
            int: 编辑距离
        """
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    async def check_emoji_file_integrity(self):
        """检查表情包文件完整性
        遍历self.emoji_objects中的所有对象，检查文件是否存在
        如果文件已被删除，则执行对象的删除方法并从列表中移除
        """
        try:
            if not self.emoji_objects:
                logger.warning("[检查] emoji_objects为空，跳过完整性检查")
                return

            total_count = len(self.emoji_objects)
            self.emoji_num = total_count
            removed_count = 0
            # 使用列表复制进行遍历，因为我们会在遍历过程中修改列表
            objects_to_remove = []
            for emoji in self.emoji_objects:
                try:
                    # 跳过已经标记为删除的，避免重复处理
                    if emoji.is_deleted:
                        objects_to_remove.append(emoji)  # 收集起来一次性移除
                        continue

                    # 检查文件是否存在
                    if not os.path.exists(emoji.full_path):
                        logger.warning(f"[检查] 表情包文件丢失: {emoji.full_path}")
                        # 执行表情包对象的删除方法
                        await emoji.delete()  # delete 方法现在会标记 is_deleted
                        objects_to_remove.append(emoji)  # 标记删除后，也收集起来移除
                        # 更新计数
                        self.emoji_num -= 1
                        removed_count += 1
                        continue

                    # 检查描述是否为空 (如果为空也视为无效)
                    if not emoji.description:
                        logger.warning(f"[检查] 表情包描述为空，视为无效: {emoji.filename}")
                        await emoji.delete()
                        objects_to_remove.append(emoji)
                        self.emoji_num -= 1
                        removed_count += 1
                        continue

                except Exception as item_error:
                    logger.error(f"[错误] 处理表情包记录时出错 ({emoji.filename}): {str(item_error)}")
                    # 即使出错，也尝试继续检查下一个
                    continue

            # 从 self.emoji_objects 中移除标记的对象
            if objects_to_remove:
                self.emoji_objects = [e for e in self.emoji_objects if e not in objects_to_remove]

            # 清理 EMOJI_REGISTED_DIR 目录中未被追踪的文件
            await self.clean_unused_emojis(EMOJI_REGISTED_DIR, self.emoji_objects)

            # 输出清理结果
            if removed_count > 0:
                logger.success(f"[清理] 已清理 {removed_count} 个失效/文件丢失的表情包记录")
                logger.info(f"[统计] 清理前记录数: {total_count} | 清理后有效记录数: {len(self.emoji_objects)}")
            else:
                logger.info(f"[检查] 已检查 {total_count} 个表情包记录，全部完好")

        except Exception as e:
            logger.error(f"[错误] 检查表情包完整性失败: {str(e)}")
            logger.error(traceback.format_exc())

    async def start_periodic_check_register(self):
        """定期检查表情包完整性和数量"""
        await self.get_all_emoji_from_db()
        while True:
            logger.info("[扫描] 开始检查表情包完整性...")
            await self.check_emoji_file_integrity()
            await self.clear_temp_emoji()
            logger.info("[扫描] 开始扫描新表情包...")

            # 检查表情包目录是否存在
            if not os.path.exists(EMOJI_DIR):
                logger.warning(f"[警告] 表情包目录不存在: {EMOJI_DIR}")
                os.makedirs(EMOJI_DIR, exist_ok=True)
                logger.info(f"[创建] 已创建表情包目录: {EMOJI_DIR}")
                await asyncio.sleep(global_config.EMOJI_CHECK_INTERVAL * 60)
                continue

            # 检查目录是否为空
            files = os.listdir(EMOJI_DIR)
            if not files:
                logger.warning(f"[警告] 表情包目录为空: {EMOJI_DIR}")
                await asyncio.sleep(global_config.EMOJI_CHECK_INTERVAL * 60)
                continue

            # 检查是否需要处理表情包(数量超过最大值或不足)
            if (self.emoji_num > self.emoji_num_max and global_config.max_reach_deletion) or (
                self.emoji_num < self.emoji_num_max
            ):
                try:
                    # 获取目录下所有图片文件
                    files_to_process = [
                        f
                        for f in files
                        if os.path.isfile(os.path.join(EMOJI_DIR, f))
                        and f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
                    ]

                    # 处理每个符合条件的文件
                    for filename in files_to_process:
                        # 尝试注册表情包
                        success = await self.register_emoji_by_filename(filename)
                        if success:
                            # 注册成功则清除失败计数并跳出循环（每轮最多注册一个新表情）
                            self._register_fail_counts.pop(filename, None)
                            break
                        else:
                            fail_count = self._register_fail_counts.get(filename, 0) + 1
                            self._register_fail_counts[filename] = fail_count
                            if fail_count >= 3:
                                # 连续3轮扫描都失败：判定为坏文件或永久不过审，删除以让队列前进
                                file_path = os.path.join(EMOJI_DIR, filename)
                                try:
                                    os.remove(file_path)
                                    logger.warning(f"[清理] 连续{fail_count}轮注册失败，删除文件: {filename}")
                                except OSError as e:
                                    logger.error(f"[错误] 删除失败文件出错: {filename}: {e}")
                                self._register_fail_counts.pop(filename, None)
                            else:
                                # 前2次失败：临时故障（API报错/限流）可能性大，保留重试，继续下一个文件不阻塞队列
                                logger.warning(
                                    f"[注册失败] 第{fail_count}/3次: {filename}（保留重试，继续处理后续文件）"
                                )
                                continue
                except Exception as e:
                    logger.error(f"[错误] 扫描表情包目录失败: {str(e)}")

            await asyncio.sleep(global_config.EMOJI_CHECK_INTERVAL * 60)

    async def get_all_emoji_from_db(self):
        """获取所有表情包并初始化为MaiEmoji类对象，更新 self.emoji_objects"""
        try:
            self._ensure_db()
            logger.info("[数据库] 开始加载所有表情包记录...")

            all_emoji_data = list(db.emoji.find())
            emoji_objects = []
            load_errors = 0

            for emoji_data in all_emoji_data:
                full_path = emoji_data.get("full_path")
                if not full_path:
                    # 尝试从旧版本路径构造完整路径
                    filename = emoji_data.get("filename")
                    path = emoji_data.get("path")
                    if filename and path:
                        full_path = os.path.join(path, filename)
                        if os.path.exists(full_path):
                            logger.info(f"[兼容模式] 自动补全旧版本表情包路径: {full_path}")
                        else:
                            logger.warning(f"[加载错误] 数据库记录缺少 'full_path' 字段且无法自动补全: {emoji_data.get('_id')}")
                            load_errors += 1
                            continue
                    else:
                        logger.warning(f"[加载错误] 数据库记录缺少 'full_path' 字段且无法自动补全: {emoji_data.get('_id')}")
                        load_errors += 1
                        continue

                try:
                    # 使用 full_path 初始化 MaiEmoji 对象
                    emoji = MaiEmoji(full_path=full_path)

                    # 设置从数据库加载的属性
                    emoji.hash = emoji_data.get("hash", "")
                    # 如果 hash 为空，也跳过？取决于业务逻辑
                    if not emoji.hash:
                        logger.warning(f"[加载错误] 数据库记录缺少 'hash' 字段: {full_path}")
                        load_errors += 1
                        continue

                    emoji.description = emoji_data.get("description", "")
                    emoji.emotion = emoji_data.get("emotion", [])
                    emoji.usage_count = emoji_data.get("usage_count", 0)
                    # 优先使用 last_used_time，否则用 timestamp，最后用当前时间
                    last_used = emoji_data.get("last_used_time")
                    timestamp = emoji_data.get("timestamp")
                    emoji.last_used_time = (
                        last_used if last_used is not None else (timestamp if timestamp is not None else time.time())
                    )
                    emoji.register_time = timestamp if timestamp is not None else time.time()
                    emoji.format = emoji_data.get("format", "")  # 加载格式

                    # 不需要再手动设置 path 和 filename，__init__ 会自动处理

                    emoji_objects.append(emoji)

                except ValueError as ve:  # 捕获 __init__ 可能的错误
                    logger.error(f"[加载错误] 初始化 MaiEmoji 失败 ({full_path}): {ve}")
                    load_errors += 1
                except Exception as e:
                    logger.error(f"[加载错误] 处理数据库记录时出错 ({full_path}): {str(e)}")
                    load_errors += 1

            # 更新内存中的列表和数量
            self.emoji_objects = emoji_objects
            self.emoji_num = len(emoji_objects)

            logger.success(f"[数据库] 加载完成: 共加载 {self.emoji_num} 个表情包记录。")
            if load_errors > 0:
                logger.warning(f"[数据库] 加载过程中出现 {load_errors} 个错误。")

        except Exception as e:
            logger.error(f"[错误] 从数据库加载所有表情包对象失败: {str(e)}")
            self.emoji_objects = []  # 加载失败则清空列表
            self.emoji_num = 0

    async def get_emoji_from_db(self, hash=None):
        """获取指定哈希值的表情包并初始化为MaiEmoji类对象列表 (主要用于调试或特定查找)

        参数:
            hash: 可选，如果提供则只返回指定哈希值的表情包

        返回:
            list[MaiEmoji]: 表情包对象列表
        """
        try:
            self._ensure_db()

            query = {}
            if hash:
                query = {"hash": hash}
            else:
                logger.warning(
                    "[查询] 未提供 hash，将尝试加载所有表情包，建议使用 get_all_emoji_from_db 更新管理器状态。"
                )

            emoji_data_list = list(db.emoji.find(query))
            emoji_objects = []
            load_errors = 0

            for emoji_data in emoji_data_list:
                full_path = emoji_data.get("full_path")
                if not full_path:
                    # 尝试从旧版本路径构造完整路径
                    filename = emoji_data.get("filename")
                    path = emoji_data.get("path")
                    if filename and path:
                        full_path = os.path.join(path, filename)
                        if os.path.exists(full_path):
                            logger.info(f"[兼容模式] 自动补全旧版本表情包路径: {full_path}")
                        else:
                            logger.warning(f"[加载错误] 数据库记录缺少 'full_path' 字段且无法自动补全: {emoji_data.get('_id')}")
                            load_errors += 1
                            continue
                    else:
                        logger.warning(f"[加载错误] 数据库记录缺少 'full_path' 字段且无法自动补全: {emoji_data.get('_id')}")
                        load_errors += 1
                        continue

                try:
                    emoji = MaiEmoji(full_path=full_path)
                    emoji.hash = emoji_data.get("hash", "")
                    if not emoji.hash:
                        logger.warning(f"[加载错误] 数据库记录缺少 'hash' 字段: {full_path}")
                        load_errors += 1
                        continue

                    emoji.description = emoji_data.get("description", "")
                    emoji.emotion = emoji_data.get("emotion", [])
                    emoji.usage_count = emoji_data.get("usage_count", 0)
                    last_used = emoji_data.get("last_used_time")
                    timestamp = emoji_data.get("timestamp")
                    emoji.last_used_time = (
                        last_used if last_used is not None else (timestamp if timestamp is not None else time.time())
                    )
                    emoji.register_time = timestamp if timestamp is not None else time.time()
                    emoji.format = emoji_data.get("format", "")
                    emoji_objects.append(emoji)
                except ValueError as ve:
                    logger.error(f"[加载错误] 初始化 MaiEmoji 失败 ({full_path}): {ve}")
                    load_errors += 1
                except Exception as e:
                    logger.error(f"[加载错误] 处理数据库记录时出错 ({full_path}): {str(e)}")
                    load_errors += 1

            if load_errors > 0:
                logger.warning(f"[查询] 加载过程中出现 {load_errors} 个错误。")

            return emoji_objects

        except Exception as e:
            logger.error(f"[错误] 从数据库获取表情包对象失败: {str(e)}")
            return []

    async def get_emoji_from_manager(self, hash) -> Optional[MaiEmoji]:
        """从内存中的 emoji_objects 列表获取表情包

        参数:
            hash: 要查找的表情包哈希值
        返回:
            MaiEmoji 或 None: 如果找到则返回 MaiEmoji 对象，否则返回 None
        """
        for emoji in self.emoji_objects:
            # 确保对象未被标记为删除且哈希值匹配
            if not emoji.is_deleted and emoji.hash == hash:
                return emoji
        return None  # 如果循环结束还没找到，则返回 None

    async def delete_emoji(self, emoji_hash: str) -> bool:
        """根据哈希值删除表情包

        Args:
            emoji_hash: 表情包的哈希值

        Returns:
            bool: 是否成功删除
        """
        try:
            self._ensure_db()

            # 从emoji_objects中查找表情包对象
            emoji = await self.get_emoji_from_manager(emoji_hash)

            if not emoji:
                logger.warning(f"[警告] 未找到哈希值为 {emoji_hash} 的表情包")
                return False

            # 使用MaiEmoji对象的delete方法删除表情包
            success = await emoji.delete()

            if success:
                # 从emoji_objects列表中移除该对象
                self.emoji_objects = [e for e in self.emoji_objects if e.hash != emoji_hash]
                # 更新计数
                self.emoji_num -= 1
                logger.info(f"[统计] 当前表情包数量: {self.emoji_num}")

                return True
            else:
                logger.error(f"[错误] 删除表情包失败: {emoji_hash}")
                return False

        except Exception as e:
            logger.error(f"[错误] 删除表情包失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _emoji_objects_to_readable_list(self, emoji_objects):
        """将表情包对象列表转换为可读的字符串列表

        参数:
            emoji_objects: MaiEmoji对象列表

        返回:
            list[str]: 可读的表情包信息字符串列表
        """
        emoji_info_list = []
        for i, emoji in enumerate(emoji_objects):
            # 转换时间戳为可读时间
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(emoji.register_time))
            # 构建每个表情包的信息字符串
            emoji_info = (
                f"编号: {i + 1}\n描述: {emoji.description}\n使用次数: {emoji.usage_count}\n添加时间: {time_str}\n"
            )
            emoji_info_list.append(emoji_info)
        return emoji_info_list

    async def replace_a_emoji(self, new_emoji: MaiEmoji):
        """替换一个表情包

        Args:
            new_emoji: 新表情包对象

        Returns:
            bool: 是否成功替换表情包
        """
        try:
            self._ensure_db()

            # 获取所有表情包对象
            emoji_objects = self.emoji_objects
            # 计算每个表情包的选择概率
            probabilities = [1 / (emoji.usage_count + 1) for emoji in emoji_objects]
            # 归一化概率，确保总和为1
            total_probability = sum(probabilities)
            normalized_probabilities = [p / total_probability for p in probabilities]

            # 使用概率分布选择最多20个表情包
            selected_emojis = random.choices(
                emoji_objects, weights=normalized_probabilities, k=min(MAX_EMOJI_FOR_PROMPT, len(emoji_objects))
            )

            # 将表情包信息转换为可读的字符串
            emoji_info_list = self._emoji_objects_to_readable_list(selected_emojis)

            # 构建提示词
            prompt = (
                f"{global_config.BOT_NICKNAME}的表情包存储已满({self.emoji_num}/{self.emoji_num_max})，"
                f"需要决定是否删除一个旧表情包来为新表情包腾出空间。\n\n"
                f"新表情包信息：\n"
                f"描述: {new_emoji.description}\n\n"
                f"现有表情包列表：\n" + "\n".join(emoji_info_list) + "\n\n"
                "请决定：\n"
                "1. 是否要删除某个现有表情包来为新表情包腾出空间？\n"
                "2. 如果要删除，应该删除哪一个(给出编号)？\n"
                "请只回答：'不删除'或'删除编号X'(X为表情包编号)。"
            )

            # 调用大模型进行决策
            decision, _ = await self.llm_emotion_judge.generate_response_async(prompt, temperature=0.8)
            logger.info(f"[决策] 结果: {decision}")

            # 解析决策结果
            if "不删除" in decision:
                logger.info("[决策] 不删除任何表情包")
                return False

            # 尝试从决策中提取表情包编号
            match = re.search(r"删除编号(\d+)", decision)
            if match:
                emoji_index = int(match.group(1)) - 1  # 转换为0-based索引

                # 检查索引是否有效
                if 0 <= emoji_index < len(selected_emojis):
                    emoji_to_delete = selected_emojis[emoji_index]

                    # 删除选定的表情包
                    logger.info(f"[决策] 删除表情包: {emoji_to_delete.description}")
                    delete_success = await self.delete_emoji(emoji_to_delete.hash)

                    if delete_success:
                        # 修复：等待异步注册完成
                        register_success = await new_emoji.register_to_db()
                        if register_success:
                            self.emoji_objects.append(new_emoji)
                            self.emoji_num += 1
                            logger.success(f"[成功] 注册: {new_emoji.filename}")
                            return True
                        else:
                            logger.error(f"[错误] 注册表情包到数据库失败: {new_emoji.filename}")
                            return False
                    else:
                        logger.error("[错误] 删除表情包失败，无法完成替换")
                        return False
                else:
                    logger.error(f"[错误] 无效的表情包编号: {emoji_index + 1}")
            else:
                logger.error(f"[错误] 无法从决策中提取表情包编号: {decision}")

            return False

        except Exception as e:
            logger.error(f"[错误] 替换表情包失败: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def build_emoji_description(self, image_base64: str) -> Tuple[str, list]:
        """获取表情包描述和情感列表

        Args:
            image_base64: 图片的base64编码

        Returns:
            Tuple[str, list]: 返回表情包描述和情感列表
        """
        try:
            # 解码图片并获取格式
            image_bytes = base64.b64decode(image_base64)
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 调用AI获取描述
            if image_format == "gif" or image_format == "GIF":
                image_base64 = image_manager.transform_gif(image_base64)
                prompt = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，描述一下表情包表达的情感和内容，描述细节，优先从互联网梗,meme的角度去分析"
                description, _ = await self.vlm.generate_response_for_image(prompt, image_base64, "jpg")
            else:
                prompt = "这是一个表情包，请详细描述一下表情包所表达的情感和内容，描述细节，优先从互联网梗,meme的角度去分析"
                description, _ = await self.vlm.generate_response_for_image(prompt, image_base64, image_format)

            # 审核表情包
            if global_config.EMOJI_CHECK:
                prompt = f'''
                    这是一个表情包，请对这个表情包进行审核，标准如下：
                    1. 必须符合"{global_config.EMOJI_CHECK_PROMPT}"的要求
                    2. 不能是色情、暴力、等违法违规内容，必须符合公序良俗
                    3. 不能是任何形式的截图，聊天记录或视频截图
                    4. 不要出现5个以上文字
                    请回答这个表情包是否满足上述要求，是则回答是，否则回答否，不要出现任何其他内容
                '''
                content, _ = await self.vlm.generate_response_for_image(prompt, image_base64, image_format)
                if content == "否":
                    # 哨兵标记：内容不符合检查标准（老大拍板：不过审重试也不会过，上层立即删除省 token）
                    return "__CHECK_REJECTED__", []

            # 分析情感含义
            emotion_prompt = f"""
            请你识别这个表情包的含义和适用场景，给我简短的描述，每个描述不要超过15个字
            这是一个基于这个表情包的描述：'{description}'
            你可以关注其幽默和讽刺意味，动用贴吧，微博，小红书的知识，必须从互联网梗,meme的角度去分析
            请直接输出描述，不要出现任何其他内容，如果有多个描述，可以用逗号分隔
            """
            emotions_text, _ = await self.llm_emotion_judge.generate_response_async(emotion_prompt, temperature=0.7)

            # 处理情感列表
            emotions = [e.strip() for e in emotions_text.split(",") if e.strip()]

            # 根据情感标签数量随机选择喵~超过5个选3个，超过2个选2个
            if len(emotions) > 5:
                emotions = random.sample(emotions, 3)
            elif len(emotions) > 2:
                emotions = random.sample(emotions, 2)

            return f"[表情包：{description}]", emotions

        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "", []

    async def register_emoji_by_filename(self, filename: str) -> bool:
        """读取指定文件名的表情包图片，分析并注册到数据库

        Args:
            filename: 表情包文件名，必须位于EMOJI_DIR目录下

        Returns:
            bool: 注册是否成功
        """
        file_full_path = os.path.join(EMOJI_DIR, filename)
        if not os.path.exists(file_full_path):
            logger.error(f"[注册失败] 文件不存在: {file_full_path}")
            return False

        try:
            # 1. 创建 MaiEmoji 实例并初始化哈希和格式
            new_emoji = MaiEmoji(full_path=file_full_path)
            init_result = await new_emoji.initialize_hash_format()
            if init_result is None or new_emoji.is_deleted:  # 初始化失败或文件读取错误
                logger.error(f"[注册失败] 初始化哈希和格式失败: {filename}")
                # 是否需要删除源文件？看业务需求，暂时不删
                return False

            # 2. 检查哈希是否已存在 (在内存中检查)
            if await self.get_emoji_from_manager(new_emoji.hash):
                logger.warning(f"[注册跳过] 表情包已存在 (Hash: {new_emoji.hash}): {filename}")
                # 删除重复的源文件
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除重复的待注册文件: {filename}")
                except Exception as e:
                    logger.error(f"[错误] 删除重复文件失败: {str(e)}")
                return False  # 返回 False 表示未注册新表情

            # 3. 构建描述和情感
            try:
                emoji_base64 = image_path_to_base64(file_full_path)
                if emoji_base64 is None:  # 再次检查读取
                    logger.error(f"[注册失败] 无法读取图片以生成描述: {filename}")
                    return False
                description, emotions = await self.build_emoji_description(emoji_base64)
                if description == "__CHECK_REJECTED__":
                    # 不过审的图重试也不会过，立即删除停止烧 token（老大拍板）
                    try:
                        os.remove(file_full_path)
                        logger.warning(f"[审核拒绝] 内容不符合检查标准，删除文件: {filename}")
                    except OSError as e:
                        logger.error(f"[错误] 删除不过审文件出错: {filename}: {e}")
                    self._register_fail_counts.pop(filename, None)
                    return False
                if not description:  # 检查描述是否成功生成（API抖动/空返回，保留重试）
                    # ⚠ 描述失败≠表情包有问题（可能是API报错/限流），保留文件下次扫描重试，不再删除
                    logger.warning(f"[注册跳过] 未能生成有效描述: {filename}（文件保留，等待下次扫描重试）")
                    return False
                new_emoji.description = description
                new_emoji.emotion = emotions
            except Exception as build_desc_error:
                # ⚠ 描述异常≠表情包有问题（API报错/网络抖动），保留文件下次扫描重试，不再删除
                logger.error(f"[注册跳过] 生成描述/情感时出错 ({filename}): {build_desc_error}（文件保留，等待下次扫描重试）")
                return False

            # 4. 检查容量并决定是否替换或直接注册
            if self.emoji_num >= self.emoji_num_max:
                logger.warning(f"表情包数量已达到上限({self.emoji_num}/{self.emoji_num_max})，尝试替换...")
                replaced = await self.replace_a_emoji(new_emoji)
                if not replaced:
                    logger.error("[注册失败] 替换表情包失败，无法完成注册")
                    # 替换失败，删除新表情包文件
                    try:
                        os.remove(file_full_path)  # new_emoji 的 full_path 此时还是源路径
                        logger.info(f"[清理] 删除替换失败的新表情文件: {filename}")
                    except Exception as e:
                        logger.error(f"[错误] 删除替换失败文件时出错: {str(e)}")
                    return False
                # 替换成功时，replace_a_emoji 内部已处理 new_emoji 的注册和添加到列表
                return True
            else:
                # 直接注册
                register_success = await new_emoji.register_to_db()  # 此方法会移动文件并更新 DB
                if register_success:
                    # 注册成功后，添加到内存列表
                    self.emoji_objects.append(new_emoji)
                    self.emoji_num += 1
                    logger.success(f"[成功] 注册新表情包: {filename} (当前: {self.emoji_num}/{self.emoji_num_max})")
                    return True
                else:
                    logger.error(f"[注册失败] 保存表情包到数据库/移动文件失败: {filename}")
                    # register_to_db 失败时，内部会尝试清理移动后的文件，源文件可能还在
                    # 是否需要删除源文件？
                    if os.path.exists(file_full_path):
                        try:
                            os.remove(file_full_path)
                            logger.info(f"[清理] 删除注册失败的源文件: {filename}")
                        except Exception as e:
                            logger.error(f"[错误] 删除注册失败源文件时出错: {str(e)}")
                    return False

        except Exception as e:
            logger.error(f"[错误] 注册表情包时发生未预期错误 ({filename}): {str(e)}")
            logger.error(traceback.format_exc())
            # 尝试删除源文件以避免循环处理
            if os.path.exists(file_full_path):
                try:
                    os.remove(file_full_path)
                    logger.info(f"[清理] 删除处理异常的源文件: {filename}")
                except Exception as remove_error:
                    logger.error(f"[错误] 删除异常处理文件时出错: {remove_error}")
            return False

    async def clear_temp_emoji(self):
        """清理临时表情包
        清理/data/emoji和/data/image目录下的所有文件
        当目录中文件数超过100时，会全部删除
        """

        logger.info("[清理] 开始清理缓存...")

        # 清理emoji目录
        emoji_dir = os.path.join(BASE_DIR, "emoji")
        if os.path.exists(emoji_dir):
            files = os.listdir(emoji_dir)
            # 如果文件数超过50就全部删除
            if len(files) > 100:
                for filename in files:
                    file_path = os.path.join(emoji_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        logger.debug(f"[清理] 删除: {filename}")

        # 清理image目录
        image_dir = os.path.join(BASE_DIR, "image")
        if os.path.exists(image_dir):
            files = os.listdir(image_dir)
            # 如果文件数超过50就全部删除
            if len(files) > 100:
                for filename in files:
                    file_path = os.path.join(image_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        logger.debug(f"[清理] 删除图片: {filename}")

        logger.success("[清理] 完成")

    async def clean_unused_emojis(self, emoji_dir, emoji_objects):
        """清理指定目录中未被 emoji_objects 追踪的表情包文件"""
        if not os.path.exists(emoji_dir):
            logger.warning(f"[清理] 目标目录不存在，跳过清理: {emoji_dir}")
            return

        try:
            # 获取内存中所有有效表情包的完整路径集合
            tracked_full_paths = {emoji.full_path for emoji in emoji_objects if not emoji.is_deleted}
            cleaned_count = 0

            # 遍历指定目录中的所有文件
            for file_name in os.listdir(emoji_dir):
                file_full_path = os.path.join(emoji_dir, file_name)

                # 确保处理的是文件而不是子目录
                if not os.path.isfile(file_full_path):
                    continue

                # 如果文件不在被追踪的集合中，则删除
                if file_full_path not in tracked_full_paths:
                    try:
                        os.remove(file_full_path)
                        logger.info(f"[清理] 删除未追踪的表情包文件: {file_full_path}")
                        cleaned_count += 1
                    except Exception as e:
                        logger.error(f"[错误] 删除文件时出错 ({file_full_path}): {str(e)}")

            if cleaned_count > 0:
                logger.success(f"[清理] 在目录 {emoji_dir} 中清理了 {cleaned_count} 个破损表情包。")
            else:
                logger.info(f"[清理] 目录 {emoji_dir} 中没有需要清理的。")

        except Exception as e:
            logger.error(f"[错误] 清理未使用表情包文件时出错 ({emoji_dir}): {str(e)}")


# 创建全局单例
emoji_manager = EmojiManager()
