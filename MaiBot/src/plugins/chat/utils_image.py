import base64
import os
import time
import hashlib
from typing import Optional
from PIL import Image
import io
import numpy as np


from ...common.database import db
from ...config.config import global_config
from ..models.utils_model import LLMRequest

from src.common.logger_manager import get_logger

logger = get_logger("chat_image")


class ImageManager:
    _instance = None
    IMAGE_DIR = "data"  # 图像存储根目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._ensure_image_collection()
            self._ensure_description_collection()
            self._ensure_image_dir()
            self._initialized = True
            self._llm = LLMRequest(model=global_config.vlm, temperature=0.4, max_tokens=300, request_type="image")

    def _ensure_image_dir(self):
        """确保图像存储目录存在"""
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

    @staticmethod
    def _ensure_image_collection():
        """确保images集合存在并创建索引"""
        if "images" not in db.list_collection_names():
            db.create_collection("images")

        # 删除旧索引
        db.images.drop_indexes()
        # 创建新的复合索引
        db.images.create_index([("hash", 1), ("type", 1)], unique=True)
        db.images.create_index([("url", 1)])
        db.images.create_index([("path", 1)])

    @staticmethod
    def _ensure_description_collection():
        """确保image_descriptions集合存在并创建索引"""
        if "image_descriptions" not in db.list_collection_names():
            db.create_collection("image_descriptions")

        # 删除旧索引
        db.image_descriptions.drop_indexes()
        # 创建新的复合索引
        db.image_descriptions.create_index([("hash", 1), ("type", 1)], unique=True)

    @staticmethod
    def _get_description_from_db(image_hash: str, description_type: str) -> Optional[str]:
        """从数据库获取图片描述

        Args:
            image_hash: 图片哈希值
            description_type: 描述类型 ('emoji' 或 'image')

        Returns:
            Optional[str]: 描述文本，如果不存在则返回None
        """
        result = db.image_descriptions.find_one({"hash": image_hash, "type": description_type})
        return result["description"] if result else None

    @staticmethod
    def _save_description_to_db(image_hash: str, description: str, description_type: str) -> None:
        """保存图片描述到数据库

        Args:
            image_hash: 图片哈希值
            description: 描述文本
            description_type: 描述类型 ('emoji' 或 'image')
        """
        try:
            db.image_descriptions.update_one(
                {"hash": image_hash, "type": description_type},
                {
                    "$set": {
                        "description": description,
                        "timestamp": int(time.time()),
                        "hash": image_hash,  # 确保hash字段存在
                        "type": description_type,  # 确保type字段存在
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.error(f"保存描述到数据库失败: {str(e)}")

    @staticmethod
    def _get_image_path_from_db(image_hash: str) -> str:
        """查图片在磁盘上的保存路径（save_pic 开着时才有值）"""
        try:
            doc = db.images.find_one({"hash": image_hash}, {"path": 1})
            return str(doc.get("path") or "") if doc else ""
        except Exception:
            return ""

    async def get_emoji_description(self, image_base64: str) -> str:
        """获取表情包描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, "emoji")
            if cached_description:
                # logger.debug(f"缓存表情包描述: {cached_description}")
                return f"[表达了：{cached_description}]"

            # 调用AI获取描述
            if image_format == "gif" or image_format == "GIF":
                image_base64 = self.transform_gif(image_base64)
                prompt = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，使用1-2个词描述一下表情包表达的情感和内容和含义，简短一些"
                description, _ = await self._llm.generate_response_for_image(prompt, image_base64, "jpg")
            else:
                prompt = "这是一个表情包，请用使用几个词描述一下表情包所表达的情感和内容和含义，简短一些"
                description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)

            cached_description = self._get_description_from_db(image_hash, "emoji")
            if cached_description:
                logger.warning(f"虽然生成了描述，但是找到缓存表情包描述: {cached_description}")
                return f"[表达了：{cached_description}]"

            # 根据配置决定是否保存图片
            if global_config.save_emoji:
                # 生成文件名和路径
                timestamp = int(time.time())
                filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
                if not os.path.exists(os.path.join(self.IMAGE_DIR, "emoji")):
                    os.makedirs(os.path.join(self.IMAGE_DIR, "emoji"))
                file_path = os.path.join(self.IMAGE_DIR, "emoji", filename)

                try:
                    # 保存文件
                    with open(file_path, "wb") as f:
                        f.write(image_bytes)

                    # 保存到数据库
                    image_doc = {
                        "hash": image_hash,
                        "path": file_path,
                        "type": "emoji",
                        "description": description,
                        "timestamp": timestamp,
                    }
                    db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                    logger.trace(f"保存表情包: {file_path}")
                except Exception as e:
                    logger.error(f"保存表情包文件失败: {str(e)}")

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, "emoji")

            return f"[表情包：{description}]"
        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "[表情包]"

    def _save_image_file(self, image_bytes: bytes, image_hash: str, image_format: str, description: str) -> str:
        """把图片字节落盘+登记 db.images，返回文件路径（失败返回空串）。

        save_pic 曾关闭期间看过的图没有落盘记录——缓存命中时也会走这里补存。
        """
        try:
            timestamp = int(time.time())
            filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
            dir_path = os.path.join(self.IMAGE_DIR, "image")
            os.makedirs(dir_path, exist_ok=True)
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "wb") as f:
                f.write(image_bytes)
            db.images.update_one(
                {"hash": image_hash},
                {
                    "$set": {
                        "hash": image_hash,
                        "path": file_path,
                        "type": "image",
                        "description": description,
                        "timestamp": timestamp,
                    }
                },
                upsert=True,
            )
            logger.trace(f"保存图片: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"保存图片文件失败: {str(e)}")
            return ""

    async def get_image_description(self, image_base64: str) -> str:
        """获取普通图片描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, "image")
            if cached_description:
                logger.debug(f"图片描述缓存中 {cached_description}")
                # 缓存命中也要带文件路径（模型要用它填 post_qzone 的 image_path）
                cached_path = self._get_image_path_from_db(image_hash)
                if not cached_path and global_config.save_pic:
                    # 历史遗留：描述有缓存但 save_pic 关闭期间从没落盘——当场补存
                    cached_path = self._save_image_file(image_bytes, image_hash, image_format, cached_description)
                return f"[图片：{cached_description}（图片文件: {cached_path}）]" if cached_path else f"[图片：{cached_description}]"

            # 调用AI获取描述
            prompt = (
                "请用中文描述这张图片的内容。如果有文字，请把文字都描述出来。并尝试猜测这个图片的含义，为后续思考聊天提供参考资料。最多100个字。"
            )
            description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)

            logger.debug(f"描述是{description}")

            if description is None:
                logger.warning("AI未能生成图片描述")
                return "[图片]"

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, "image")

            # 落盘并带路径返回：模型看到路径才能把这张图发进空间（post_qzone 的 image_path）
            file_path = ""
            if global_config.save_pic:
                file_path = self._save_image_file(image_bytes, image_hash, image_format, description)
            return f"[图片：{description}（图片文件: {file_path}）]" if file_path else f"[图片：{description}]"
        except Exception as e:
            logger.error(f"获取图片描述失败: {str(e)}")
            return "[图片]"

    @staticmethod
    def transform_gif(gif_base64: str, similarity_threshold: float = 1000.0, max_frames: int = 15) -> Optional[str]:
        """将GIF转换为水平拼接的静态图像, 跳过相似的帧

        Args:
            gif_base64: GIF的base64编码字符串
            similarity_threshold: 判定帧相似的阈值 (MSE)，越小表示要求差异越大才算不同帧，默认1000.0
            max_frames: 最大抽取的帧数，默认15

        Returns:
            Optional[str]: 拼接后的JPG图像的base64编码字符串, 或者在失败时返回None
        """
        try:
            # 解码base64
            gif_data = base64.b64decode(gif_base64)
            gif = Image.open(io.BytesIO(gif_data))

            # 收集所有帧
            all_frames = []
            try:
                while True:
                    gif.seek(len(all_frames))
                    # 确保是RGB格式方便比较
                    frame = gif.convert("RGB")
                    all_frames.append(frame.copy())
            except EOFError:
                pass  # 读完啦

            if not all_frames:
                logger.warning("GIF中没有找到任何帧")
                return None  # 空的GIF直接返回None

            # --- 新的帧选择逻辑 ---
            selected_frames = []
            last_selected_frame_np = None

            for i, current_frame in enumerate(all_frames):
                current_frame_np = np.array(current_frame)

                # 第一帧总是要选的
                if i == 0:
                    selected_frames.append(current_frame)
                    last_selected_frame_np = current_frame_np
                    continue

                # 计算和上一张选中帧的差异（均方误差 MSE）
                if last_selected_frame_np is not None:
                    mse = np.mean((current_frame_np - last_selected_frame_np) ** 2)
                    # logger.trace(f"帧 {i} 与上一选中帧的 MSE: {mse}") # 可以取消注释来看差异值

                    # 如果差异够大，就选它！
                    if mse > similarity_threshold:
                        selected_frames.append(current_frame)
                        last_selected_frame_np = current_frame_np
                        # 检查是不是选够了
                        if len(selected_frames) >= max_frames:
                            # logger.debug(f"已选够 {max_frames} 帧，停止选择。")
                            break
                # 如果差异不大就跳过这一帧啦

            # --- 帧选择逻辑结束 ---

            # 如果选择后连一帧都没有（比如GIF只有一帧且后续处理失败？）或者原始GIF就没帧，也返回None
            if not selected_frames:
                logger.warning("处理后没有选中任何帧")
                return None

            # logger.debug(f"总帧数: {len(all_frames)}, 选中帧数: {len(selected_frames)}")

            # 获取选中的第一帧的尺寸（假设所有帧尺寸一致）
            frame_width, frame_height = selected_frames[0].size

            # 计算目标尺寸，保持宽高比
            target_height = 200  # 固定高度
            # 防止除以零
            if frame_height == 0:
                logger.error("帧高度为0，无法计算缩放尺寸")
                return None
            target_width = int((target_height / frame_height) * frame_width)
            # 宽度也不能是0
            if target_width == 0:
                logger.warning(f"计算出的目标宽度为0 (原始尺寸 {frame_width}x{frame_height})，调整为1")
                target_width = 1

            # 调整所有选中帧的大小
            resized_frames = [
                frame.resize((target_width, target_height), Image.Resampling.LANCZOS) for frame in selected_frames
            ]

            # 创建拼接图像
            total_width = target_width * len(resized_frames)
            # 防止总宽度为0
            if total_width == 0 and len(resized_frames) > 0:
                logger.warning("计算出的总宽度为0，但有选中帧，可能目标宽度太小")
                # 至少给点宽度吧
                total_width = len(resized_frames)
            elif total_width == 0:
                logger.error("计算出的总宽度为0且无选中帧")
                return None

            combined_image = Image.new("RGB", (total_width, target_height))

            # 水平拼接图像
            for idx, frame in enumerate(resized_frames):
                combined_image.paste(frame, (idx * target_width, 0))

            # 转换为base64
            buffer = io.BytesIO()
            combined_image.save(buffer, format="JPEG", quality=85)  # 保存为JPEG
            result_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            return result_base64

        except MemoryError:
            logger.error("GIF转换失败: 内存不足，可能是GIF太大或帧数太多")
            return None  # 内存不够啦
        except Exception as e:
            logger.error(f"GIF转换失败: {str(e)}", exc_info=True)  # 记录详细错误信息
            return None  # 其他错误也返回None


# 创建全局单例
image_manager = ImageManager()


def image_path_to_base64(image_path: str) -> str:
    """将图片路径转换为base64编码
    Args:
        image_path: 图片文件路径
    Returns:
        str: base64编码的图片数据
    Raises:
        FileNotFoundError: 当图片文件不存在时
        IOError: 当读取图片文件失败时
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    with open(image_path, "rb") as f:
        image_data = f.read()
        if not image_data:
            raise IOError(f"读取图片文件失败: {image_path}")
        return base64.b64encode(image_data).decode("utf-8")
