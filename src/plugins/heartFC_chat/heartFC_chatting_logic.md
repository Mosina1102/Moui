# HeartFChatting 逻辑详解

`HeartFChatting` 类是心流系统（Heart Flow System）中实现**专注聊天**（`ChatState.FOCUSED`）功能的核心。顾名思义，其职责乃是在特定聊天流（`stream_id`）中，模拟更为连贯深入之对话。此非凭空臆造，而是依赖一个持续不断的 **思考(Think)-规划(Plan)-执行(Execute)** 循环。当其所系的 `SubHeartflow` 进入 `FOCUSED` 状态时，便会创建并启动 `HeartFChatting` 实例；若状态转为他途（譬如 `CHAT` 或 `ABSENT`），则会将其关闭。

## 1. 初始化简述 (`__init__`, `_initialize`)

创生之初，`HeartFChatting` 需注入若干关键之物：`chat_id`（亦即 `stream_id`）、关联的 `SubMind` 实例，以及 `Observation` 实例（用以观察环境）。

其内部核心组件包括：

-   `ActionManager`: 管理当前循环可选之策（如：不应、言语、表情）。
-   `HeartFCGenerator` (`self.gpt_instance`): 专司生成回复文本之职。
-   `ToolUser` (`self.tool_user`): 虽主要用于获取工具定义，然亦备 `SubMind` 调用之需（实际执行由 `SubMind` 操持）。
-   `HeartFCSender` (`self.heart_fc_sender`): 负责消息发送诸般事宜，含"正在思考"之态。
-   `LLMRequest` (`self.planner_llm`): 配置用于执行"规划"任务的大语言模型。

*初始化过程采取懒加载策略，仅在首次需要访问 `ChatStream` 时（通常在 `start` 方法中）进行。*

## 2. 生命周期 (`start`, `shutdown`)

-   **启动 (`start`)**: 外部调用此法，以启 `HeartFChatting` 之流程。内部会安全地启动主循环任务。
-   **关闭 (`shutdown`)**: 外部调用此法，以止其运行。会取消主循环任务，清理状态，并释放锁。

## 3. 核心循环 (`_hfc_loop`) 与 循环记录 (`CycleInfo`)

`_hfc_loop` 乃 `HeartFChatting` 之脉搏，以异步方式不舍昼夜运行（直至 `shutdown` 被调用）。其核心在于周而复始地执行 **思考-规划-执行** 之周期。

每一轮循环，皆会创建一个 `CycleInfo` 对象。此对象犹如史官，详细记载该次循环之点滴：

-   **身份标识**: 循环 ID (`cycle_id`)。
-   **时间轨迹**: 起止时刻 (`start_time`, `end_time`)。
-   **行动细节**: 是否执行动作 (`action_taken`)、动作类型 (`action_type`)、决策理由 (`reasoning`)。
-   **耗时考量**: 各阶段计时 (`timers`)。
-   **关联信息**: 思考消息 ID (`thinking_id`)、是否重新规划 (`replanned`)、详尽响应信息 (`response_info`，含生成文本、表情、锚点、实际发送ID、`SubMind`思考等)。

这些 `CycleInfo` 被存入一个队列 (`_cycle_history`)，近者得观。此记录不仅便于调试，更关键的是，它会作为**上下文信息**传递给下一次循环的"思考"阶段，使得 `SubMind` 能鉴往知来，做出更连贯的决策。

*循环间会根据执行情况智能引入延迟，避免空耗资源。*

## 4. 思考-规划-执行周期 (`_think_plan_execute_loop`)

此乃 `HeartFChatting` 最核心的逻辑单元，每一循环皆按序执行以下三步：

### 4.1. 思考 (`_get_submind_thinking`)

*   **第一步：观察环境**: 调用 `Observation` 的 `observe()` 方法，感知聊天室是否有新动态（如新消息）。
*   **第二步：触发子思维**: 调用关联 `SubMind` 的 `do_thinking_before_reply()` 方法。
    *   **关键点**: 会将**上一个循环**的 `CycleInfo` 传入，让 `SubMind` 了解上次行动的决策、理由及是否重新规划，从而实现"承前启后"的思考。
    *   `SubMind` 在此阶段不仅进行思考，还可能**调用其配置的工具**来收集信息。
*   **第三步：获取成果**: `SubMind` 返回两部分重要信息：
    1.  当前的内心想法 (`current_mind`)。
    2.  通过工具调用收集到的结构化信息 (`structured_info`)。

### 4.2. 规划 (`_planner`)

*   **输入**: 接收来自"思考"阶段的 `current_mind` 和 `structured_info`，以及"观察"到的最新消息。
*   **目标**: 基于当前想法、已知信息、聊天记录、机器人个性以及可用动作，决定**接下来要做什么**。
*   **决策方式**:
    1.  构建一个精心设计的提示词 (`_build_planner_prompt`)。
    2.  获取 `ActionManager` 中定义的当前可用动作（如 `no_reply`, `text_reply`, `emoji_reply`）作为"工具"选项。
    3.  调用大语言模型 (`self.planner_llm`)，**强制**其选择一个动作"工具"并提供理由。可选动作包括：
        *   `no_reply`: 不回复（例如，自己刚说过话或对方未回应）。
        *   `text_reply`: 发送文本回复。
        *   `emoji_reply`: 仅发送表情。
        *   文本回复亦可附带表情（通过 `emoji_query` 参数指定）。
*   **动态调整（重新规划）**:
    *   在做出初步决策后，会检查自规划开始后是否有新消息 (`_check_new_messages`)。
    *   若有新消息，则有一定概率触发**重新规划**。此时会再次调用规划器，但提示词会包含之前决策的信息，要求 LLM 重新考虑。
*   **输出**: 返回一个包含最终决策的字典，主要包括：
    *   `action`: 选定的动作类型。
    *   `reasoning`: 做出此决策的理由。
    *   `emoji_query`: (可选) 如果需要发送表情，指定表情的主题。

### 4.3. 执行 (`_handle_action`)

*   **输入**: 接收"规划"阶段输出的 `action`、`reasoning` 和 `emoji_query`。
*   **行动**: 根据 `action` 的类型，分派到不同的处理函数：
    *   **文本回复 (`_handle_text_reply`)**:
        1.  获取锚点消息（当前实现为系统触发的占位符）。
        2.  调用 `HeartFCSender` 的 `register_thinking` 标记开始思考。
        3.  调用 `HeartFCGenerator` (`_replier_work`) 生成回复文本。**注意**: 回复器逻辑 (`_replier_work`) 本身并非独立复杂组件，主要是调用 `HeartFCGenerator` 完成文本生成。
        4.  调用 `HeartFCSender` (`_sender`) 发送生成的文本和可能的表情。**注意**: 发送逻辑 (`_sender`, `_send_response_messages`, `_handle_emoji`) 同样委托给 `HeartFCSender` 实例处理，包含模拟打字、实际发送、存储消息等细节。
    *   **仅表情回复 (`_handle_emoji_reply`)**:
        1.  获取锚点消息。
        2.  调用 `HeartFCSender` 发送表情。
    *   **不回复 (`_handle_no_reply`)**:
        1.  记录理由。
        2.  进入等待状态 (`_wait_for_new_message`)，直到检测到新消息或超时（目前300秒），期间会监听关闭信号。

## 总结

`HeartFChatting` 通过 **观察 -> 思考（含工具）-> 规划 -> 执行** 的闭环，并利用 `CycleInfo` 进行上下文传递，实现了更加智能和连贯的专注聊天行为。其核心在于利用 `SubMind` 进行深度思考和信息收集，再通过 LLM 规划器进行决策，最后由 `HeartFCSender` 可靠地执行消息发送任务。
