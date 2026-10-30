import asyncio
import time
import random
from typing import Dict, Any, Optional, List, Tuple
import json  # 导入 json 模块
import functools  # <-- 新增导入

# 导入日志模块
from src.common.logger_manager import get_logger

# 导入聊天流管理模块
from src.plugins.chat.chat_stream import chat_manager

# 导入心流相关类
from src.heart_flow.sub_heartflow import SubHeartflow, ChatState
from src.heart_flow.mai_state_manager import MaiStateInfo
from .observation import ChattingObservation

# 导入LLM请求工具
from src.plugins.models.utils_model import LLMRequest
from src.config.config import global_config
from src.individuality.individuality import Individuality
import traceback


# 初始化日志记录器

logger = get_logger("subheartflow_manager")

# 子心流管理相关常量
INACTIVE_THRESHOLD_SECONDS = 3600  # 子心流不活跃超时时间(秒)
NORMAL_CHAT_TIMEOUT_SECONDS = 15 * 60  # 15分钟


class SubHeartflowManager:
    """管理所有活跃的 SubHeartflow 实例。"""

    def __init__(self, mai_state_info: MaiStateInfo):
        self.subheartflows: Dict[Any, "SubHeartflow"] = {}
        self._lock = asyncio.Lock()  # 用于保护 self.subheartflows 的访问
        self.mai_state_info: MaiStateInfo = mai_state_info  # 存储传入的 MaiStateInfo 实例

        # 为 LLM 状态评估创建一个 LLMRequest 实例
        # 使用与 Heartflow 相同的模型和参数
        self.llm_state_evaluator = LLMRequest(
            model=global_config.llm_heartflow,  # 与 Heartflow 一致
            temperature=0.6,  # 与 Heartflow 一致
            max_tokens=1000,  # 与 Heartflow 一致 (虽然可能不需要这么多)
            request_type="subheartflow_state_eval",  # 保留特定的请求类型
        )

    def get_all_subheartflows(self) -> List["SubHeartflow"]:
        """获取所有当前管理的 SubHeartflow 实例列表 (快照)。"""
        return list(self.subheartflows.values())

    async def get_or_create_subheartflow(self, subheartflow_id: Any) -> Optional["SubHeartflow"]:
        """获取或创建指定ID的子心流实例

        Args:
            subheartflow_id: 子心流唯一标识符
            # mai_states 参数已被移除，使用 self.mai_state_info

        Returns:
            成功返回SubHeartflow实例，失败返回None
        """
        async with self._lock:
            # 检查是否已存在该子心流
            if subheartflow_id in self.subheartflows:
                subflow = self.subheartflows[subheartflow_id]
                if subflow.should_stop:
                    logger.warning(f"尝试获取已停止的子心流 {subheartflow_id}，正在重新激活")
                    subflow.should_stop = False  # 重置停止标志

                subflow.last_active_time = time.time()  # 更新活跃时间
                # logger.debug(f"获取到已存在的子心流: {subheartflow_id}")
                return subflow

            try:
                # --- 使用 functools.partial 创建 HFC 回调 --- #
                # 将 manager 的 _handle_hfc_no_reply 方法与当前的 subheartflow_id 绑定
                hfc_callback = functools.partial(self._handle_hfc_no_reply, subheartflow_id)
                # --- 结束创建回调 --- #

                # 初始化子心流, 传入 mai_state_info 和 partial 创建的回调
                new_subflow = SubHeartflow(
                    subheartflow_id,
                    self.mai_state_info,
                    hfc_callback,  # <-- 传递 partial 创建的回调
                )

                # 异步初始化
                await new_subflow.initialize()

                # 添加聊天观察者
                observation = ChattingObservation(chat_id=subheartflow_id)
                # 回拨2分钟：覆盖"消息进来到实例创建"的时间差，第一条消息也进观察（空唤醒根因修复）
                observation.last_observe_time = time.time() - 120
                await observation.initialize()  # 拉取初始历史（此前从未调用→进FOCUSED后丢失CHAT期间的聊天记录）
                new_subflow.add_observation(observation)

                # 注册子心流
                self.subheartflows[subheartflow_id] = new_subflow
                heartflow_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
                logger.info(f"[{heartflow_name}] 开始接收消息")

                # 启动后台任务
                asyncio.create_task(new_subflow.subheartflow_start_working())

                return new_subflow
            except Exception as e:
                logger.error(f"创建子心流 {subheartflow_id} 失败: {e}", exc_info=True)
                return None

    # --- 新增：内部方法，用于尝试将单个子心流设置为 ABSENT ---
    async def _try_set_subflow_absent_internal(self, subflow: "SubHeartflow", log_prefix: str) -> bool:
        """
        尝试将给定的子心流对象状态设置为 ABSENT (内部方法，不处理锁)。

        Args:
            subflow: 子心流对象。
            log_prefix: 用于日志记录的前缀 (例如 "[子心流管理]" 或 "[停用]")。

        Returns:
            bool: 如果状态成功变为 ABSENT 或原本就是 ABSENT，返回 True；否则返回 False。
        """
        flow_id = subflow.subheartflow_id
        stream_name = chat_manager.get_stream_name(flow_id) or flow_id

        if subflow.chat_state.chat_status != ChatState.ABSENT:
            logger.debug(f"{log_prefix} 设置 {stream_name} 状态为 ABSENT")
            try:
                await subflow.change_chat_state(ChatState.ABSENT)
                # 再次检查以确认状态已更改 (change_chat_state 内部应确保)
                if subflow.chat_state.chat_status == ChatState.ABSENT:
                    return True
                else:
                    logger.warning(
                        f"{log_prefix} 调用 change_chat_state 后，{stream_name} 状态仍为 {subflow.chat_state.chat_status.value}"
                    )
                    return False
            except Exception as e:
                logger.error(f"{log_prefix} 设置 {stream_name} 状态为 ABSENT 时失败: {e}", exc_info=True)
                return False
        else:
            logger.debug(f"{log_prefix} {stream_name} 已是 ABSENT 状态")
            return True  # 已经是目标状态，视为成功

    # --- 结束新增 ---

    async def sleep_subheartflow(self, subheartflow_id: Any, reason: str) -> bool:
        """停止指定的子心流并将其状态设置为 ABSENT"""
        log_prefix = "[子心流管理]"
        async with self._lock:  # 加锁以安全访问字典
            subheartflow = self.subheartflows.get(subheartflow_id)

            stream_name = chat_manager.get_stream_name(subheartflow_id) or subheartflow_id
            logger.info(f"{log_prefix} 正在停止 {stream_name}, 原因: {reason}")

            # 调用内部方法处理状态变更
            success = await self._try_set_subflow_absent_internal(subheartflow, log_prefix)

            return success
        # 锁在此处自动释放

    def get_inactive_subheartflows(self, max_age_seconds=INACTIVE_THRESHOLD_SECONDS):
        """识别并返回需要清理的不活跃(处于ABSENT状态超过一小时)子心流(id, 原因)"""
        current_time = time.time()
        flows_to_stop = []

        for subheartflow_id, subheartflow in list(self.subheartflows.items()):
            state = subheartflow.chat_state.chat_status
            if state != ChatState.ABSENT:
                continue
            subheartflow.update_last_chat_state_time()
            absent_last_time = subheartflow.chat_state_last_time
            if max_age_seconds and (current_time - absent_last_time) > max_age_seconds:
                flows_to_stop.append(subheartflow_id)

        return flows_to_stop

    async def enforce_subheartflow_limits(self):
        """根据主状态限制停止超额子心流(优先停不活跃的)"""
        # 使用 self.mai_state_info 获取当前状态和限制
        current_mai_state = self.mai_state_info.get_current_state()
        normal_limit = current_mai_state.get_normal_chat_max_num()
        focused_limit = current_mai_state.get_focused_chat_max_num()
        logger.debug(f"[限制] 状态:{current_mai_state.value}, 普通限:{normal_limit}, 专注限:{focused_limit}")

        # 分类统计当前子心流
        normal_flows = []
        focused_flows = []
        for flow_id, flow in list(self.subheartflows.items()):
            if flow.chat_state.chat_status == ChatState.CHAT:
                normal_flows.append((flow_id, getattr(flow, "last_active_time", 0)))
            elif flow.chat_state.chat_status == ChatState.FOCUSED:
                focused_flows.append((flow_id, getattr(flow, "last_active_time", 0)))

        logger.debug(f"[限制] 当前数量 - 普通:{len(normal_flows)}, 专注:{len(focused_flows)}")
        stopped = 0

        # 处理普通聊天超额
        if len(normal_flows) > normal_limit:
            excess = len(normal_flows) - normal_limit
            logger.info(f"[限制] 普通聊天超额({len(normal_flows)}>{normal_limit}), 停止{excess}个")
            normal_flows.sort(key=lambda x: x[1])
            for flow_id, _ in normal_flows[:excess]:
                if await self.sleep_subheartflow(flow_id, f"普通聊天超额(限{normal_limit})"):
                    stopped += 1

        # 处理专注聊天超额(需重新统计)
        focused_flows = [
            (fid, t)
            for fid, f in list(self.subheartflows.items())
            if (t := getattr(f, "last_active_time", 0)) and f.chat_state.chat_status == ChatState.FOCUSED
        ]
        if len(focused_flows) > focused_limit:
            excess = len(focused_flows) - focused_limit
            logger.info(f"[限制] 专注聊天超额({len(focused_flows)}>{focused_limit}), 停止{excess}个")
            focused_flows.sort(key=lambda x: x[1])
            for flow_id, _ in focused_flows[:excess]:
                if await self.sleep_subheartflow(flow_id, f"专注聊天超额(限{focused_limit})"):
                    stopped += 1

        if stopped:
            logger.info(f"[限制] 已停止{stopped}个子心流, 剩余:{len(self.subheartflows)}")
        else:
            logger.debug(f"[限制] 无需停止, 当前总数:{len(self.subheartflows)}")

    async def deactivate_all_subflows(self):
        """将所有子心流的状态更改为 ABSENT (例如主状态变为OFFLINE时调用)"""
        log_prefix = "[停用]"
        changed_count = 0
        processed_count = 0

        async with self._lock:  # 获取锁以安全迭代
            # 使用 list() 创建一个当前值的快照，防止在迭代时修改字典
            flows_to_update = list(self.subheartflows.values())
            processed_count = len(flows_to_update)
            if not flows_to_update:
                logger.debug(f"{log_prefix} 无活跃子心流，无需操作")
                return

            for subflow in flows_to_update:
                # 记录原始状态，以便统计实际改变的数量
                original_state_was_absent = subflow.chat_state.chat_status == ChatState.ABSENT

                success = await self._try_set_subflow_absent_internal(subflow, log_prefix)

                # 如果成功设置为 ABSENT 且原始状态不是 ABSENT，则计数
                if success and not original_state_was_absent:
                    if subflow.chat_state.chat_status == ChatState.ABSENT:
                        changed_count += 1
                    else:
                        # 这种情况理论上不应发生，如果内部方法返回 True 的话
                        stream_name = chat_manager.get_stream_name(subflow.subheartflow_id) or subflow.subheartflow_id
                        logger.warning(f"{log_prefix} 内部方法声称成功但 {stream_name} 状态未变为 ABSENT。")
        # 锁在此处自动释放

        logger.info(
            f"{log_prefix} 完成，共处理 {processed_count} 个子心流，成功将 {changed_count} 个非 ABSENT 子心流的状态更改为 ABSENT。"
        )

    async def sbhf_absent_into_focus(self):
        """评估子心流兴趣度，满足条件且未达上限则提升到FOCUSED状态（基于start_hfc_probability）"""
        try:
            log_prefix = "[兴趣评估]"
            # 使用 self.mai_state_info 获取当前状态和限制
            current_state = self.mai_state_info.get_current_state()
            focused_limit = current_state.get_focused_chat_max_num()

            # --- 新增：检查是否允许进入 FOCUS 模式 --- #
            if not global_config.allow_focus_mode:
                if int(time.time()) % 60 == 0:  # 每60秒输出一次日志避免刷屏
                    logger.debug(f"{log_prefix} 配置不允许进入 FOCUSED 状态 (allow_focus_mode=False)")
                return  # 如果不允许，直接返回
            # --- 结束新增 ---

            logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 可以在{focused_limit}个群激情聊天")

            if focused_limit <= 0:
                # logger.debug(f"{log_prefix} 当前状态 ({current_state.value}) 不允许 FOCUSED 子心流")
                return

            current_focused_count = self.count_subflows_by_state(ChatState.FOCUSED)
            if current_focused_count >= focused_limit:
                logger.debug(f"{log_prefix} 已达专注上限 ({current_focused_count}/{focused_limit})")
                return

            for sub_hf in list(self.subheartflows.values()):
                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id

                logger.debug(f"{log_prefix} 检查子心流: {stream_name}，现在状态: {sub_hf.chat_state.chat_status.value}")

                # 跳过非CHAT状态或已经是FOCUSED状态的子心流
                if sub_hf.chat_state.chat_status == ChatState.FOCUSED:
                    continue

                from .mai_state_manager import enable_unlimited_hfc_chat

                if not enable_unlimited_hfc_chat:
                    if sub_hf.chat_state.chat_status != ChatState.CHAT:
                        continue

                # 检查是否满足提升概率
                logger.debug(
                    f"{log_prefix} 检查子心流: {stream_name}，现在概率: {sub_hf.interest_chatting.start_hfc_probability}"
                )
                if random.random() >= sub_hf.interest_chatting.start_hfc_probability:
                    continue

                # 再次检查是否达到上限
                if current_focused_count >= focused_limit:
                    logger.debug(f"{log_prefix} [{stream_name}] 已达专注上限")
                    break

                # 获取最新状态并执行提升
                current_subflow = self.subheartflows.get(flow_id)
                if not current_subflow:
                    continue

                logger.info(
                    f"{log_prefix} [{stream_name}] 触发 认真水群 (概率={current_subflow.interest_chatting.start_hfc_probability:.2f})"
                )

                # 执行状态提升
                await current_subflow.change_chat_state(ChatState.FOCUSED)

                # 验证提升结果
                if (
                    final_subflow := self.subheartflows.get(flow_id)
                ) and final_subflow.chat_state.chat_status == ChatState.FOCUSED:
                    current_focused_count += 1
        except Exception as e:
            logger.error(f"启动HFC 兴趣评估失败: {e}", exc_info=True)

    async def sbhf_absent_into_chat(self):
        """
        随机选一个 ABSENT 状态的子心流，评估是否应转换为 CHAT 状态。
        每次调用最多转换一个。
        """
        current_mai_state = self.mai_state_info.get_current_state()
        chat_limit = current_mai_state.get_normal_chat_max_num()

        async with self._lock:
            # 1. 筛选出所有 ABSENT 状态的子心流
            absent_subflows = [
                hf for hf in self.subheartflows.values() if hf.chat_state.chat_status == ChatState.ABSENT
            ]

            if not absent_subflows:
                logger.debug("没有摸鱼的子心流可以评估。")  # 日志太频繁，注释掉
                return  # 没有目标，直接返回

            # 2. 随机选一个幸运儿
            sub_hf_to_evaluate = random.choice(absent_subflows)
            flow_id = sub_hf_to_evaluate.subheartflow_id
            stream_name = chat_manager.get_stream_name(flow_id) or flow_id
            log_prefix = f"[{stream_name}]"

            # 2.5 检查最近是否有新消息（优化：避免无消息时调用LLM）
            CHECK_NEW_MESSAGE_THRESHOLD_SECONDS = 300  # 5分钟内有新消息才考虑聊天
            if not sub_hf_to_evaluate.observations:
                logger.debug(f"{log_prefix} 没有观察者，跳过评估")
                return
            first_observation = sub_hf_to_evaluate.observations[0]
            has_new_msg = await first_observation.has_new_messages_since(
                time.time() - CHECK_NEW_MESSAGE_THRESHOLD_SECONDS
            )
            if not has_new_msg:
                logger.debug(f"{log_prefix} 最近{CHECK_NEW_MESSAGE_THRESHOLD_SECONDS}秒没有新消息，跳过LLM评估")
                return

            # 3. 检查 CHAT 上限
            current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)
            if current_chat_count >= chat_limit:
                logger.info(f"{log_prefix} 想看看能不能聊，但是聊天太多了， ({current_chat_count}/{chat_limit}) 满了。")
                return  # 满了，这次就算了

            # --- 获取 FOCUSED 计数 ---
            current_focused_count = self.count_subflows_by_state_nolock(ChatState.FOCUSED)
            focused_limit = current_mai_state.get_focused_chat_max_num()

            # --- 新增：获取聊天和专注群名 ---
            chatting_group_names = []
            focused_group_names = []
            for flow_id, hf in self.subheartflows.items():
                stream_name = chat_manager.get_stream_name(flow_id) or str(flow_id)  # 保证有名字
                if hf.chat_state.chat_status == ChatState.CHAT:
                    chatting_group_names.append(stream_name)
                elif hf.chat_state.chat_status == ChatState.FOCUSED:
                    focused_group_names.append(stream_name)
            # --- 结束新增 ---

            # --- 决定"要不要开始聊" ---
            # 统一大脑模式：跳过 LLM 评估（老大拍板砍冗余——"说不说"本来就该由统一大脑决定，
            # 状态机点头后统一大脑大概率 no_reply，双重决策白花钱还拖慢进群速度）。
            # 零成本放行：有新消息（上面查过）+没满员 → 直接进 CHAT。
            if getattr(global_config, "enable_group_unified_brain", False):
                yao_kai_shi_liao_ma = True
                reason = "统一大脑模式：状态机零成本放行，说不说交给统一大脑"
            else:
                # --- 获取观察信息和构建 Prompt ---
                await first_observation.observe()
                current_chat_log = first_observation.talking_message_str or "当前没啥聊天内容。"
                _observation_summary = f"最近聊了这些:\n{current_chat_log}"

                mai_state_description = f"你当前状态: {current_mai_state.value}。"
                individuality = Individuality.get_instance()
                personality_prompt = individuality.get_prompt(x_person=2, level=2)
                prompt_personality = f"你正在扮演名为{individuality.name}的人类，{personality_prompt}"

                # --- 修改：在 prompt 中加入当前聊天计数和群名信息 (条件显示) ---
                chat_status_lines = []
                if chatting_group_names:
                    chat_status_lines.append(
                        f"正在闲聊 ({current_chat_count}/{chat_limit}): {', '.join(chatting_group_names)}"
                    )
                if focused_group_names:
                    chat_status_lines.append(
                        f"正在专注 ({current_focused_count}/{focused_limit}): {', '.join(focused_group_names)}"
                    )

                chat_status_prompt = "当前没有在任何群聊中。"  # 默认消息喵~
                if chat_status_lines:
                    chat_status_prompt = "当前聊天情况：\n" + "\n".join(chat_status_lines)  # 拼接状态信息

                prompt = (
                    f"{prompt_personality}\\n"
                    f"你当前没在 [{stream_name}] 群聊天。\\n"
                    f"{mai_state_description}\\n"
                    f"{chat_status_prompt}\\n"  # <-- 喵！用了新的状态信息~
                    f"{_observation_summary}\\n---\\n"
                    f"基于以上信息，你想不想开始在这个群闲聊？\\n"
                    f"请说明理由，并以 JSON 格式回答，包含 'decision' (布尔值) 和 'reason' (字符串)。\\n"
                    f'例如：{{"decision": true, "reason": "看起来挺热闹的，插个话"}}\\n'
                    f'例如：{{"decision": false, "reason": "已经聊了好多，休息一下"}}\\n'
                    f"请只输出有效的 JSON 对象。"
                )
                # --- 结束修改 ---

                # --- 4. LLM 评估是否想聊 ---
                yao_kai_shi_liao_ma, reason = await self._llm_evaluate_state_transition(prompt)

            if reason:
                if yao_kai_shi_liao_ma:
                    logger.info(f"{log_prefix} 打算开始聊，原因是: {reason}")
                else:
                    logger.info(f"{log_prefix} 不打算聊，原因是: {reason}")
            else:
                logger.info(f"{log_prefix} 结果: {yao_kai_shi_liao_ma}")

            if yao_kai_shi_liao_ma is None:
                logger.debug(f"{log_prefix} 问AI想不想聊失败了，这次算了。")
                return  # 评估失败，结束

            if not yao_kai_shi_liao_ma:
                # logger.info(f"{log_prefix} 现在不想聊这个群。")
                return  # 不想聊，结束

            # --- 5. AI想聊，再次检查额度并尝试转换 ---
            # 再次检查以防万一
            current_chat_count_before_change = self.count_subflows_by_state_nolock(ChatState.CHAT)
            if current_chat_count_before_change < chat_limit:
                logger.info(
                    f"{log_prefix} 想聊，而且还有精力 ({current_chat_count_before_change}/{chat_limit})，这就去聊！"
                )
                await sub_hf_to_evaluate.change_chat_state(ChatState.CHAT)
                # 确认转换成功
                if sub_hf_to_evaluate.chat_state.chat_status == ChatState.CHAT:
                    logger.debug(f"{log_prefix} 成功进入聊天状态！本次评估圆满结束。")
                else:
                    logger.warning(
                        f"{log_prefix} 奇怪，尝试进入聊天状态失败了。当前状态: {sub_hf_to_evaluate.chat_state.chat_status.value}"
                    )
            else:
                logger.warning(
                    f"{log_prefix} AI说想聊，但是刚问完就没空位了 ({current_chat_count_before_change}/{chat_limit})。真不巧，下次再说吧。"
                )
            # 无论转换成功与否，本次评估都结束了

        # 锁在这里自动释放

    # --- 新增：单独检查 CHAT 状态超时的任务 ---
    async def sbhf_chat_into_absent(self):
        """定期检查处于 CHAT 状态的子心流是否因长时间未发言而超时，并将其转为 ABSENT。"""
        log_prefix_task = "[聊天超时检查]"
        transitioned_to_absent = 0
        checked_count = 0

        async with self._lock:
            subflows_snapshot = list(self.subheartflows.values())
            checked_count = len(subflows_snapshot)

            if not subflows_snapshot:
                # logger.debug(f"{log_prefix_task} 没有子心流需要检查超时。")
                return

            for sub_hf in subflows_snapshot:
                # 只检查 CHAT 状态的子心流
                if sub_hf.chat_state.chat_status != ChatState.CHAT:
                    continue

                flow_id = sub_hf.subheartflow_id
                stream_name = chat_manager.get_stream_name(flow_id) or flow_id
                log_prefix = f"[{stream_name}]({log_prefix_task})"

                should_deactivate = False
                reason = ""

                try:
                    # 使用变量名 last_bot_dong_zuo_time 替代 last_bot_activity_time
                    last_bot_dong_zuo_time = sub_hf.get_normal_chat_last_speak_time()

                    if last_bot_dong_zuo_time > 0:
                        current_time = time.time()
                        # 使用变量名 time_since_last_bb 替代 time_since_last_reply
                        time_since_last_bb = current_time - last_bot_dong_zuo_time

                        if time_since_last_bb > NORMAL_CHAT_TIMEOUT_SECONDS:
                            should_deactivate = True
                            reason = f"超过 {NORMAL_CHAT_TIMEOUT_SECONDS / 60:.0f} 分钟没 BB"
                            logger.info(
                                f"{log_prefix} 太久没有发言 ({reason})，不看了。上次活动时间: {last_bot_dong_zuo_time:.0f}"
                            )
                        # else:
                        #     logger.debug(f"{log_prefix} Bot活动时间未超时 ({time_since_last_bb:.0f}s < {NORMAL_CHAT_TIMEOUT_SECONDS}s)，保持 CHAT 状态。")
                    # else:
                    # 如果没有记录到Bot的活动时间，暂时不因为超时而转换状态
                    # logger.debug(f"{log_prefix} 未找到有效的 Bot 最后活动时间记录，不执行超时检查。")

                except AttributeError:
                    logger.error(
                        f"{log_prefix} 无法获取 Bot 最后 BB 时间，请确保 SubHeartflow 相关实现正确。跳过超时检查。"
                    )
                except Exception as e:
                    logger.error(f"{log_prefix} 检查 Bot 超时状态时出错: {e}", exc_info=True)

                # --- 执行状态转换（如果超时） ---
                if should_deactivate:
                    logger.debug(f"{log_prefix} 因超时 ({reason})，尝试转换为 ABSENT 状态。")
                    await sub_hf.change_chat_state(ChatState.ABSENT)
                    # 再次检查确保状态已改变
                    if sub_hf.chat_state.chat_status == ChatState.ABSENT:
                        transitioned_to_absent += 1
                        logger.info(f"{log_prefix} 不看了。")
                    else:
                        logger.warning(f"{log_prefix} 尝试因超时转换为 ABSENT 失败。")

        if transitioned_to_absent > 0:
            logger.debug(
                f"{log_prefix_task} 完成，共检查 {checked_count} 个子心流，{transitioned_to_absent} 个因超时转为 ABSENT。"
            )

    # --- 结束新增 ---

    async def _llm_evaluate_state_transition(self, prompt: str) -> Tuple[Optional[bool], Optional[str]]:
        """
        使用 LLM 评估是否应进行状态转换，期望 LLM 返回 JSON 格式。

        Args:
            prompt: 提供给 LLM 的提示信息，要求返回 {"decision": true/false}。

        Returns:
            Optional[bool]: 如果成功解析 LLM 的 JSON 响应并提取了 'decision' 键的值，则返回该布尔值。
                        如果 LLM 调用失败、返回无效 JSON 或 JSON 中缺少 'decision' 键或其值不是布尔型，则返回 None。
        """
        log_prefix = "[LLM状态评估]"
        try:
            # --- 真实的 LLM 调用 ---
            response_text, _ = await self.llm_state_evaluator.generate_response_async(prompt)
            # logger.debug(f"{log_prefix} 使用模型 {self.llm_state_evaluator.model_name} 评估")
            logger.debug(f"{log_prefix} 原始输入: {prompt}")
            logger.debug(f"{log_prefix} 原始评估结果: {response_text}")

            # --- 解析 JSON 响应 ---
            try:
                # 尝试去除可能的Markdown代码块标记
                cleaned_response = response_text.strip().strip("`").strip()
                if cleaned_response.startswith("json"):
                    cleaned_response = cleaned_response[4:].strip()

                data = json.loads(cleaned_response)
                decision = data.get("decision")  # 使用 .get() 避免 KeyError
                reason = data.get("reason")

                if isinstance(decision, bool):
                    logger.debug(f"{log_prefix} LLM评估结果 (来自JSON): {'建议转换' if decision else '建议不转换'}")

                    return decision, reason
                else:
                    logger.warning(
                        f"{log_prefix} LLM 返回的 JSON 中 'decision' 键的值不是布尔型: {decision}。响应: {response_text}"
                    )
                    return None, None  # 值类型不正确

            except json.JSONDecodeError as json_err:
                logger.warning(f"{log_prefix} LLM 返回的响应不是有效的 JSON: {json_err}。响应: {response_text}")
                # 尝试在非JSON响应中查找关键词作为后备方案 (可选)
                if "true" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'true'，解释为建议转换")
                    return True, None
                if "false" in response_text.lower():
                    logger.debug(f"{log_prefix} 在非JSON响应中找到 'false'，解释为建议不转换")
                    return False, None
                return None, None  # JSON 解析失败，也未找到关键词
            except Exception as parse_err:  # 捕获其他可能的解析错误
                logger.warning(f"{log_prefix} 解析 LLM JSON 响应时发生意外错误: {parse_err}。响应: {response_text}")
                return None, None

        except Exception as e:
            logger.error(f"{log_prefix} 调用 LLM 或处理其响应时出错: {e}", exc_info=True)
            traceback.print_exc()
            return None, None  # LLM 调用或处理失败

    def count_subflows_by_state(self, state: ChatState) -> int:
        """统计指定状态的子心流数量"""
        count = 0
        # 遍历所有子心流实例
        for subheartflow in self.subheartflows.values():
            # 检查子心流状态是否匹配
            if subheartflow.chat_state.chat_status == state:
                count += 1
        return count

    def count_subflows_by_state_nolock(self, state: ChatState) -> int:
        """
        统计指定状态的子心流数量 (不上锁版本)。
        警告：仅应在已持有 self._lock 的上下文中使用此方法。
        """
        count = 0
        for subheartflow in self.subheartflows.values():
            if subheartflow.chat_state.chat_status == state:
                count += 1
        return count

    def get_active_subflow_minds(self) -> List[str]:
        """获取所有活跃(非ABSENT)子心流的当前想法"""
        minds = []
        for subheartflow in self.subheartflows.values():
            # 检查子心流是否活跃(非ABSENT状态)
            if subheartflow.chat_state.chat_status != ChatState.ABSENT:
                minds.append(subheartflow.sub_mind.current_mind)
        return minds

    def update_main_mind_in_subflows(self, main_mind: str):
        """更新所有子心流的主心流想法"""
        updated_count = sum(
            1
            for _, subheartflow in list(self.subheartflows.items())
            if subheartflow.subheartflow_id in self.subheartflows
        )
        logger.debug(f"[子心流管理器] 更新了{updated_count}个子心流的主想法")

    async def delete_subflow(self, subheartflow_id: Any):
        """删除指定的子心流。"""
        async with self._lock:
            subflow = self.subheartflows.pop(subheartflow_id, None)
            if subflow:
                logger.info(f"正在删除 SubHeartflow: {subheartflow_id}...")
                try:
                    # 调用 shutdown 方法确保资源释放
                    await subflow.shutdown()
                    logger.info(f"SubHeartflow {subheartflow_id} 已成功删除。")
                except Exception as e:
                    logger.error(f"删除 SubHeartflow {subheartflow_id} 时出错: {e}", exc_info=True)
            else:
                logger.warning(f"尝试删除不存在的 SubHeartflow: {subheartflow_id}")

    # --- 新增：处理 HFC 无回复回调的专用方法 --- #
    async def _handle_hfc_no_reply(self, subheartflow_id: Any):
        """处理来自 HeartFChatting 的连续无回复信号 (通过 partial 绑定 ID)"""
        # 注意：这里不需要再获取锁，因为 sbhf_focus_into_absent 内部会处理锁
        logger.debug(f"[管理器 HFC 处理器] 接收到来自 {subheartflow_id} 的 HFC 无回复信号")
        await self.sbhf_focus_into_absent(subheartflow_id)

    # --- 结束新增 --- #

    # --- 新增：处理来自 HeartFChatting 的状态转换请求 --- #
    async def sbhf_focus_into_absent(self, subflow_id: Any):
        """
        接收来自 HeartFChatting 的请求，将特定子心流的状态转换为 ABSENT。
        通常在连续多次 "no_reply" 后被调用。

        Args:
            subflow_id: 需要转换状态的子心流 ID。
        """
        async with self._lock:
            subflow = self.subheartflows.get(subflow_id)
            if not subflow:
                logger.warning(f"[状态转换请求] 尝试转换不存在的子心流 {subflow_id} 到 ABSENT")
                return

            stream_name = chat_manager.get_stream_name(subflow_id) or subflow_id
            current_state = subflow.chat_state.chat_status

            # 仅当子心流处于 FOCUSED 状态时才进行转换
            # 因为 HeartFChatting 只在 FOCUSED 状态下运行
            if current_state == ChatState.FOCUSED:
                target_state = ChatState.ABSENT  # 默认目标状态
                log_reason = "默认转换"

                # 决定是去 ABSENT 还是 CHAT
                if random.random() < 0.5:
                    target_state = ChatState.ABSENT
                    log_reason = "随机选择 ABSENT"
                    logger.debug(f"[状态转换请求] {stream_name} ({current_state.value}) 随机决定进入 ABSENT")
                else:
                    # 尝试进入 CHAT，先检查限制
                    current_mai_state = self.mai_state_info.get_current_state()
                    chat_limit = current_mai_state.get_normal_chat_max_num()
                    # 使用不上锁的版本，因为我们已经在锁内
                    current_chat_count = self.count_subflows_by_state_nolock(ChatState.CHAT)

                    if current_chat_count < chat_limit:
                        target_state = ChatState.CHAT
                        log_reason = f"随机选择 CHAT (当前 {current_chat_count}/{chat_limit})"
                        logger.debug(
                            f"[状态转换请求] {stream_name} ({current_state.value}) 随机决定进入 CHAT，未达上限 ({current_chat_count}/{chat_limit})"
                        )
                    else:
                        target_state = ChatState.ABSENT
                        log_reason = f"随机选择 CHAT 但已达上限 ({current_chat_count}/{chat_limit})，转为 ABSENT"
                        logger.debug(
                            f"[状态转换请求] {stream_name} ({current_state.value}) 随机决定进入 CHAT，但已达上限 ({current_chat_count}/{chat_limit})，改为进入 ABSENT"
                        )

                # 开始转换
                logger.info(
                    f"[状态转换请求] 接收到请求，将 {stream_name} (当前: {current_state.value}) 尝试转换为 {target_state.value} ({log_reason})"
                )
                try:
                    await subflow.change_chat_state(target_state)
                    # 检查最终状态
                    final_state = subflow.chat_state.chat_status
                    if final_state == target_state:
                        logger.debug(f"[状态转换请求] {stream_name} 状态已成功转换为 {final_state.value}")
                    else:
                        logger.warning(
                            f"[状态转换请求] 尝试将 {stream_name} 转换为 {target_state.value} 后，状态实际为 {final_state.value}"
                        )
                except Exception as e:
                    logger.error(
                        f"[状态转换请求] 转换 {stream_name} 到 {target_state.value} 时出错: {e}", exc_info=True
                    )
            elif current_state == ChatState.ABSENT:
                logger.debug(f"[状态转换请求] {stream_name} 已处于 ABSENT 状态，无需转换")
            else:
                logger.warning(
                    f"[状态转换请求] 收到对 {stream_name} 的请求，但其状态为 {current_state.value} (非 FOCUSED)，不执行转换"
                )

    # --- 结束新增 --- #
