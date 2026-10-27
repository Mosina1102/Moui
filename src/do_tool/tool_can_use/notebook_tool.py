import re
import json
import uuid
import time
from typing import Dict, Any, List, Optional, Tuple
from src.common.logger_manager import get_logger
from src.common.database import db

logger = get_logger("notebook_tool")


class NotebookManager:
    """记事本管理器：负责笔记的存储和检索"""
    
    _instance = None
    COLLECTION_NAME = "notebook"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _get_collection(self):
        """获取数据库集合"""
        return db[self.COLLECTION_NAME]
    
    def add_note(
        self,
        title: str,
        content: str,
        category: str = "other",
        priority: int = 3,
        chat_id: Optional[str] = None,
        expires_at: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """添加一条笔记（带简单去重）"""
        current_time = time.time()
        
        # 简单去重：5分钟内是否有完全相同的笔记
        duplicate_check_time = current_time - 300
        existing = self._get_collection().find_one({
            "title": title,
            "content": content,
            "created_at": {"$gt": duplicate_check_time},
            "is_completed": False,
        })
        
        if existing:
            logger.info(f"检测到重复笔记，跳过添加: {title}")
            existing.pop("_id", None)
            return None
        
        note_id = f"note_{uuid.uuid4().hex[:12]}"
        
        note_data = {
            "note_id": note_id,
            "title": title,
            "content": content,
            "category": category,
            "priority": max(1, min(5, priority)),
            "chat_id": chat_id,
            "created_at": current_time,
            "updated_at": current_time,
            "expires_at": expires_at,
            "is_completed": False,
            "tags": tags or [],
        }
        
        self._get_collection().insert_one(note_data)
        logger.info(f"添加笔记: {note_id} - {title}")
        
        note_data.pop("_id", None)
        return note_data
    
    def query_notes(
        self,
        category: Optional[str] = None,
        chat_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        include_completed: bool = False,
        include_expired: bool = False,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """查询笔记"""
        query = {}
        
        if category:
            query["category"] = category
        if chat_id:
            query["chat_id"] = chat_id
        if not include_completed:
            query["is_completed"] = {"$ne": True}
        if tags:
            query["tags"] = {"$in": tags}
        
        current_time = time.time()
        if not include_expired:
            query["$or"] = [
                {"expires_at": None},
                {"expires_at": {"$gt": current_time}},
            ]
        
        notes = list(self._get_collection().find(query).sort("priority", 1).sort("created_at", -1).limit(limit))
        
        for note in notes:
            note.pop("_id", None)
        
        return notes
    
    def get_note_by_id(self, note_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取笔记"""
        note = self._get_collection().find_one({"note_id": note_id})
        if note:
            note.pop("_id", None)
        return note
    
    def update_note(
        self,
        note_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[int] = None,
        expires_at: Optional[float] = None,
        tags: Optional[List[str]] = None,
        is_completed: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """更新笔记"""
        update_data = {"updated_at": time.time()}
        
        if title is not None:
            update_data["title"] = title
        if content is not None:
            update_data["content"] = content
        if category is not None:
            update_data["category"] = category
        if priority is not None:
            update_data["priority"] = max(1, min(5, priority))
        if expires_at is not None:
            update_data["expires_at"] = expires_at
        if tags is not None:
            update_data["tags"] = tags
        if is_completed is not None:
            update_data["is_completed"] = is_completed
        
        result = self._get_collection().update_one(
            {"note_id": note_id},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"更新笔记: {note_id}")
            return self.get_note_by_id(note_id)
        
        return None
    
    def delete_note(self, note_id: str) -> bool:
        """删除笔记"""
        result = self._get_collection().delete_one({"note_id": note_id})
        if result.deleted_count > 0:
            logger.info(f"删除笔记: {note_id}")
            return True
        return False
    
    def mark_completed(self, note_id: str) -> bool:
        """标记笔记为已完成"""
        result = self.update_note(note_id, is_completed=True)
        return result is not None
    
    def get_active_reminders(self, chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取活跃的提醒"""
        return self.query_notes(
            category="reminder",
            chat_id=chat_id,
            include_completed=False,
            include_expired=False,
            limit=20,
        )
    
    def get_game_states(self, chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取游戏状态"""
        return self.query_notes(
            category="game_state",
            chat_id=chat_id,
            include_completed=False,
            limit=10,
        )
    
    def cleanup_expired_notes(self) -> int:
        """清理已过期的笔记"""
        current_time = time.time()
        result = self._get_collection().delete_many({
            "expires_at": {"$lt": current_time, "$ne": None}
        })
        if result.deleted_count > 0:
            logger.info(f"清理了 {result.deleted_count} 条过期笔记")
        return result.deleted_count
    
    def get_notes_for_prompt(self, chat_id: Optional[str] = None) -> str:
        """获取用于提示词的笔记信息"""
        notes = self.query_notes(
            chat_id=chat_id,
            include_completed=False,
            include_expired=False,
            limit=10,
        )
        
        if not notes:
            return ""
        
        result = "【记事本中的内容】\n"
        category_names = {
            "reminder": "提醒",
            "game_state": "游戏状态",
            "user_info": "用户信息",
            "task": "任务",
            "other": "其他",
        }
        
        for note in notes:
            cat_name = category_names.get(note["category"], note["category"])
            result += f"- [{cat_name}] {note['title']}: {note['content']} (ID: {note['note_id']})\n"
        
        return result


class NotebookParser:
    """记事本解析器：从大模型回复中提取记事本标记并执行操作"""
    
    NOTE_PATTERN = re.compile(
        r'<note\s+([^>]*)>(.*?)</note>',
        re.DOTALL | re.IGNORECASE
    )
    
    VALID_CATEGORIES = ["reminder", "game_state", "user_info", "task", "other"]
    VALID_ACTIONS = ["add", "update", "delete", "complete"]
    
    @classmethod
    def parse_attributes(cls, attr_string: str) -> Dict[str, str]:
        """解析标签属性"""
        attrs = {}
        patterns = {
            'action': r'action\s*=\s*["\']?([^"\'>\s]+)["\']?',
            'category': r'category\s*=\s*["\']?([^"\'>\s]+)["\']?',
            'title': r'title\s*=\s*["\']([^"\']*)["\']',
            'priority': r'priority\s*=\s*["\']?(\d+)["\']?',
            'note_id': r'note_id\s*=\s*["\']?([^"\'>\s]+)["\']?',
            'expires': r'expires\s*=\s*["\']?([^"\'>\s]+)["\']?',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, attr_string, re.IGNORECASE)
            if match:
                attrs[key] = match.group(1)
        
        return attrs
    
    @classmethod
    def parse_expires(cls, expires_str: Optional[str]) -> Optional[float]:
        """解析过期时间"""
        if not expires_str:
            return None
        
        expires_str = expires_str.strip().lower()
        current_time = time.time()
        
        if expires_str.isdigit():
            return float(expires_str)
        
        match = re.match(r'(\d+)\s*(秒|分钟|小时|天|min|hour|day|h|d)', expires_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            
            if unit in ['秒']:
                return current_time + value
            elif unit in ['分钟', 'min']:
                return current_time + value * 60
            elif unit in ['小时', 'hour', 'h']:
                return current_time + value * 3600
            elif unit in ['天', 'day', 'd']:
                return current_time + value * 86400
        
        return None
    
    @classmethod
    def extract_and_process(cls, text: str, chat_id: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
        """从文本中提取记事本标记并执行操作
        
        Args:
            text: 大模型回复文本
            chat_id: 聊天流ID
            
        Returns:
            Tuple[str, List[Dict]]: (清理后的文本, 操作结果列表)
        """
        notebook = NotebookManager.get_instance()
        results = []
        
        def process_match(match):
            attr_string = match.group(1)
            content = match.group(2).strip()
            
            attrs = cls.parse_attributes(attr_string)
            action = attrs.get('action', 'add').lower()
            
            if action not in cls.VALID_ACTIONS:
                logger.warning(f"未知的记事本操作: {action}")
                return ""
            
            result = {"action": action, "success": False, "message": ""}
            
            try:
                if action == "add":
                    category = attrs.get('category', 'other').lower()
                    if category not in cls.VALID_CATEGORIES:
                        category = 'other'
                    
                    title = attrs.get('title', '未命名笔记')
                    priority = int(attrs.get('priority', 3))
                    expires_at = cls.parse_expires(attrs.get('expires'))
                    
                    note = notebook.add_note(
                        title=title,
                        content=content,
                        category=category,
                        priority=priority,
                        chat_id=chat_id,
                        expires_at=expires_at,
                    )
                    
                    if note:
                        result["success"] = True
                        result["note"] = note
                        result["message"] = f"已记录: {title}"
                        logger.info(f"[记事本] 添加笔记: {title} - {content[:30]}...")
                    else:
                        result["success"] = True
                        result["message"] = f"跳过重复记录: {title}"
                        logger.info(f"[记事本] 跳过重复笔记: {title}")
                
                elif action == "update":
                    note_id = attrs.get('note_id')
                    if not note_id:
                        result["message"] = "更新笔记需要提供 note_id"
                    else:
                        update_data = {}
                        if attrs.get('title'):
                            update_data['title'] = attrs.get('title')
                        if content:
                            update_data['content'] = content
                        if attrs.get('priority'):
                            update_data['priority'] = int(attrs.get('priority'))
                        
                        note = notebook.update_note(note_id, **update_data)
                        if note:
                            result["success"] = True
                            result["note"] = note
                            result["message"] = f"已更新: {note_id}"
                        else:
                            result["message"] = f"未找到笔记: {note_id}"
                
                elif action == "delete":
                    note_id = attrs.get('note_id')
                    if not note_id:
                        result["message"] = "删除笔记需要提供 note_id"
                    else:
                        if notebook.delete_note(note_id):
                            result["success"] = True
                            result["message"] = f"已删除: {note_id}"
                        else:
                            result["message"] = f"删除失败: {note_id}"
                
                elif action == "complete":
                    note_id = attrs.get('note_id')
                    if not note_id:
                        result["message"] = "完成笔记需要提供 note_id"
                    else:
                        if notebook.mark_completed(note_id):
                            result["success"] = True
                            result["message"] = f"已完成: {note_id}"
                        else:
                            result["message"] = f"完成失败: {note_id}"
            
            except Exception as e:
                result["message"] = f"操作失败: {str(e)}"
                logger.error(f"记事本操作失败: {e}")
            
            results.append(result)
            return ""
        
        cleaned_text = cls.NOTE_PATTERN.sub(process_match, text)
        
        return cleaned_text.strip(), results
    
    @classmethod
    def has_note_markers(cls, text: str) -> bool:
        """检查文本中是否包含记事本标记"""
        return bool(cls.NOTE_PATTERN.search(text))


def get_notebook_prompt_section() -> str:
    """获取记事本功能的提示词部分"""
    return """
【记事本功能】
你有一个记事本，可以记录重要信息。当你需要记录某些事情时，可以在回复中使用 <note> 标签，系统会自动处理。

使用格式：
<note action="操作" category="类别" title="标题" [其他属性]>内容</note>

操作类型：
- add: 添加笔记（默认）
- update: 更新笔记（需要 note_id）
- delete: 删除笔记（需要 note_id）
- complete: 标记完成（需要 note_id）

笔记类别：
- reminder: 提醒事项（如：用户让你明天提醒他）
- game_state: 游戏状态（如：成语接龙当前进度）
- user_info: 用户信息（如：新人的喜好）
- task: 待办任务
- other: 其他事项

可选属性：
- priority: 优先级 1-5（1最高，默认3）
- expires: 过期时间（如："1小时"、"2天"、时间戳）

示例：
1. 用户说"记得明天下午3点提醒我开会"，你可以回复：
   "好的，我记住了！<note action="add" category="reminder" title="开会提醒" expires="1天">明天下午3点提醒用户开会</note>"

2. 成语接龙游戏中，记录当前状态：
   <note action="add" category="game_state" title="成语接龙">当前是"马到成功"，轮到用户小明</note>

3. 标记任务完成：
   <note action="complete" note_id="note_xxx"></note>

注意：
- <note> 标签不会显示给用户，只用于系统记录
- 可以在正常回复中穿插使用
- 记录的信息会在后续对话中作为上下文提供给你
"""


def get_notebook_context(chat_id: Optional[str] = None) -> str:
    """获取记事本上下文信息，用于构建提示词"""
    notebook = NotebookManager.get_instance()
    return notebook.get_notes_for_prompt(chat_id)
