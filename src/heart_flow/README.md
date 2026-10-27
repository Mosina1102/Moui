# 心流系统 (Heart Flow System)

## 一条消息是怎么到最终回复的？简明易懂的介绍

1 接受消息，由HeartHC_processor处理消息，存储消息

    1.1 process_message()函数，接受消息

    1.2 创建消息对应的聊天流(chat_stream)和子心流(sub_heartflow)

    1.3 进行常规消息处理

    1.4 存储消息 store_message()

    1.5 计算兴趣度Interest

    1.6 将消息连同兴趣度，存储到内存中的interest_dict(SubHeartflow的属性)

2 根据 sub_heartflow 的聊天状态，决定后续处理流程

    2a ABSENT状态：不做任何处理

    2b CHAT状态：送入NormalChat 实例

    2c FOCUS状态：送入HeartFChatting 实例

b NormalChat工作方式

    b.1 启动后台任务 _reply_interested_message，持续运行。
    b.2 该任务轮询 InterestChatting 提供的 interest_dict
    b.3 对每条消息，结合兴趣度、是否被提及(@)、意愿管理器(WillingManager)计算回复概率。（这部分要改，目前还是用willing计算的，之后要和Interest合并）
    b.4 若概率通过：
        b.4.1 创建"思考中"消息 (MessageThinking)。
        b.4.2 调用 NormalChatGenerator 生成文本回复。
        b.4.3 通过 message_manager 发送回复 (MessageSending)。
        b.4.4 可能根据配置和文本内容，额外发送一个匹配的表情包。
        b.4.5 更新关系值和全局情绪。
    b.5 处理完成后，从 interest_dict 中移除该消息。

c HeartFChatting工作方式

    c.1 启动主循环 _hfc_loop
    c.2 每个循环称为一个周期 (Cycle)，执行 think_plan_execute 流程。
    c.3 Think (思考) 阶段:
        c.3.1 观察 (Observe): 通过 ChattingObservation，使用 observe() 获取最新的聊天消息。
        c.3.2 思考 (Think): 调用 SubMind 的 do_thinking_before_reply 方法。
            c.3.2.1 SubMind 结合观察到的内容、个性、情绪、上周期动作等信息，生成当前的内心想法 (current_mind)。
            c.3.2.2 在此过程中 SubMind 的LLM可能请求调用工具 (ToolUser) 来获取额外信息或执行操作，结果存储在 structured_info 中。
    c.4 Plan (规划/决策) 阶段:
        c.4.1 结合观察到的消息文本、`SubMind` 生成的 `current_mind` 和 `structured_info`、以及 `ActionManager` 提供的可用动作，决定本次周期的行动 (`text_reply`/`emoji_reply`/`no_reply`) 和理由。
        c.4.2 重新规划检查 (Re-plan Check): 如果在 c.3.1 到 c.4.1 期间检测到新消息，可能(有概率)触发重新执行 c.4.1 决策步骤。
    c.5 Execute (执行/回复) 阶段:
        c.5.1 如果决策是 text_reply:
            c.5.1.1 获取锚点消息。
            c.5.1.2 通过 HeartFCSender 注册"思考中"状态。
            c.5.1.3 调用 HeartFCGenerator (gpt_instance) 生成回复文本。
            c.5.1.4 通过 HeartFCSender 发送回复
            c.5.1.5 如果规划时指定了表情查询 (emoji_query)，随后发送表情。
        c.5.2 如果决策是 emoji_reply:
            c.5.2.1 获取锚点消息。
            c.5.2.2 通过 HeartFCSender 直接发送匹配查询 (emoji_query) 的表情。
        c.5.3 如果决策是 no_reply:
            c.5.3.1 进入等待状态，直到检测到新消息或超时。
            c.5.3.2 同时，增加内部连续不回复计数器。如果该计数器达到预设阈值（例如 5 次），则调用初始化时由 `SubHeartflowManager` 提供的回调函数。此回调函数会通知 `SubHeartflowManager` 请求将对应的 `SubHeartflow` 状态转换为 `ABSENT`。如果执行了其他动作（如 `text_reply` 或 `emoji_reply`），则此计数器会被重置。
    c.6 循环结束后，记录周期信息 (CycleInfo)，并根据情况进行短暂休眠，防止CPU空转。



## 1. 一条消息是怎么到最终回复的？复杂细致的介绍

### 1.1. 主心流 (Heartflow)
- **文件**: `heartflow.py`
- **职责**:
    - 作为整个系统的主控制器。
    - 持有并管理 `SubHeartflowManager`，用于管理所有子心流。
    - 持有并管理自身状态 `self.current_state: MaiStateInfo`，该状态控制系统的整体行为模式。
    - 统筹管理系统后台任务（如消息存储、资源分配等）。
    - **注意**: 主心流自身不进行周期性的全局思考更新。

### 1.2. 子心流 (SubHeartflow)
- **文件**: `sub_heartflow.py`
- **职责**:
    - 处理具体的交互场景，例如：群聊、私聊、与虚拟主播(vtb)互动、桌面宠物交互等。
    - 维护特定场景下的思维状态和聊天流状态 (`ChatState`)。
    - 通过关联的 `Observation` 实例接收和处理信息。
    - 拥有独立的思考 (`SubMind`) 和回复判断能力。
- **观察者**: 每个子心流可以拥有一个或多个 `Observation` 实例（目前每个子心流仅使用一个 `ChattingObservation`)。
- **内部结构**:
    - **聊天流状态 (`ChatState`)**: 标记当前子心流的参与模式 (`ABSENT`, `CHAT`, `FOCUSED`)，决定是否观察、回复以及使用何种回复模式。
    - **聊天实例 (`NormalChatInstance` / `HeartFlowChatInstance`)**: 根据 `ChatState` 激活对应的实例来处理聊天逻辑。同一时间只有一个实例处于活动状态。

### 1.3. 观察系统 (Observation)
- **文件**: `observation.py`
- **职责**:
    - 定义信息输入的来源和格式。
    - 为子心流提供其所处环境的信息。
- **当前实现**: 
    - 目前仅有 `ChattingObservation` 一种观察类型。
    - `ChattingObservation` 负责从数据库拉取指定聊天的最新消息，并将其格式化为可读内容，供 `SubHeartflow` 使用。

### 1.4. 子心流管理器 (SubHeartflowManager)
- **文件**: `subheartflow_manager.py`
- **职责**:
    - 作为 `Heartflow` 的成员变量存在。
    - **在初始化时接收并持有 `Heartflow` 的 `MaiStateInfo` 实例。**
    - 负责所有 `SubHeartflow` 实例的生命周期管理，包括：
        - 创建和获取 (`get_or_create_subheartflow`)。
        - 停止和清理 (`sleep_subheartflow`, `cleanup_inactive_subheartflows`)。
        - 根据 `Heartflow` 的状态 (`self.mai_state_info`) 和限制条件，激活、停用或调整子心流的状态（例如 `enforce_subheartflow_limits`, `randomly_deactivate_subflows`, `sbhf_absent_into_focus`）。
        - **新增**: 通过调用 `sbhf_absent_into_chat` 方法，使用 LLM (配置与 `Heartflow` 主 LLM 相同) 评估处于 `ABSENT` 或 `CHAT` 状态的子心流，根据观察到的活动摘要和 `Heartflow` 的当前状态，判断是否应在 `ABSENT` 和 `CHAT` 之间进行转换 (同样受限于 `CHAT` 状态的数量上限)。
    - **清理机制**: 通过后台任务 (`BackgroundTaskManager`) 定期调用 `cleanup_inactive_subheartflows` 方法，此方法会识别并**删除**那些处于 `ABSENT` 状态超过一小时 (`INACTIVE_THRESHOLD_SECONDS`) 的子心流实例。

### 1.5. 消息处理与回复流程 (Message Processing vs. Replying Flow)
- **关注点分离**: 系统严格区分了接收和处理传入消息的流程与决定和生成回复的流程。
    - **消息处理 (Processing)**: 
        - 由一个独立的处理器（例如 `HeartFCProcessor`）负责接收原始消息数据。
        - 职责包括：消息解析 (`MessageRecv`)、过滤（屏蔽词、正则表达式）、基于记忆系统的初步兴趣计算 (`HippocampusManager`)、消息存储 (`MessageStorage`) 以及用户关系更新 (`RelationshipManager`)。
        - 处理后的消息信息（如计算出的兴趣度）会传递给对应的 `SubHeartflow`。
    - **回复决策与生成 (Replying)**:
        - 由 `SubHeartflow` 及其当前激活的聊天实例 (`NormalChatInstance` 或 `HeartFlowChatInstance`) 负责。
        - 基于其内部状态 (`ChatState`、`SubMind` 的思考结果)、观察到的信息 (`Observation` 提供的内容) 以及 `InterestChatting` 的状态来决定是否回复、何时回复以及如何回复。
- **消息缓冲 (Message Caching)**:
    - `message_buffer` 模块会对某些传入消息进行临时缓存，尤其是在处理连续的多部分消息（如多张图片）时。
    - 这个缓冲机制发生在 `HeartFCProcessor` 处理流程中，确保消息的完整性，然后才进行后续的存储和兴趣计算。
    - 缓存的消息最终仍会流向对应的 `ChatStream`（与 `SubHeartflow` 关联），但核心的消息处理与回复决策仍然是分离的步骤。

## 2. 核心控制与状态管理 (Core Control and State Management)

### 2.1. Heart Flow 整体控制
- **控制者**: 主心流 (`Heartflow`)
- **核心职责**:
    - 通过其成员 `SubHeartflowManager` 创建和管理子心流（**在创建 `SubHeartflowManager` 时会传入自身的 `MaiStateInfo`**）。
    - 通过其成员 `self.current_state: MaiStateInfo` 控制整体行为模式。
    - 管理系统级后台任务。
    - **注意**: 不再提供直接获取所有子心流 ID (`get_all_subheartflows_streams_ids`) 的公共方法。

### 2.2. Heart Flow 状态 (`MaiStateInfo`)
- **定义与管理**: `Heartflow` 持有 `MaiStateInfo` 的实例 (`self.current_state`) 来管理其状态。状态的枚举定义在 `my_state_manager.py` 中的 `MaiState`。
- **状态及含义**:
    - `MaiState.OFFLINE` (不在线): 不观察任何群消息，不进行主动交互，仅存储消息。当主状态变为 `OFFLINE` 时，`SubHeartflowManager` 会将所有子心流的状态设置为 `ChatState.ABSENT`。
    - `MaiState.PEEKING` (看一眼手机): 有限度地参与聊天（由 `MaiStateInfo` 定义具体的普通/专注群数量限制）。
    - `MaiState.NORMAL_CHAT` (正常看手机): 正常参与聊天，允许 `SubHeartflow` 进入 `CHAT` 或 `FOCUSED` 状态（数量受限）。
    *   `MaiState.FOCUSED_CHAT` (专心看手机): 更积极地参与聊天，通常允许更多或更高优先级的 `FOCUSED` 状态子心流。
- **当前转换逻辑**: 目前，`MaiState` 之间的转换由 `MaiStateManager` 管理，主要基于状态持续时间和随机概率。这是一种临时的实现方式，未来计划进行改进。
- **作用**: `Heartflow` 的状态直接影响 `SubHeartflowManager` 如何管理子心流（如激活数量、允许的状态等）。

### 2.3. 聊天流状态 (`ChatState`) 与转换
- **管理对象**: 每个 `SubHeartflow` 实例内部维护其 `ChatStateInfo`，包含当前的 `ChatState`。
- **状态及含义**:
    - `ChatState.ABSENT` (不参与/没在看): 初始或停用状态。子心流不观察新信息，不进行思考，也不回复。
    - `ChatState.CHAT` (随便看看/水群): 普通聊天模式。激活 `NormalChatInstance`。
    *   `ChatState.FOCUSED` (专注/认真水群): 专注聊天模式。激活 `HeartFlowChatInstance`。
- **选择**: 子心流可以根据外部指令（来自 `SubHeartflowManager`）或内部逻辑（未来的扩展）选择进入 `ABSENT` 状态（不回复不观察），或进入 `CHAT` / `FOCUSED` 中的一种回复模式。
- **状态转换机制** (由 `SubHeartflowManager` 驱动，更细致的说明):
    - **初始状态**: 新创建的 `SubHeartflow` 默认为 `ABSENT` 状态。
    - **`ABSENT` -> `CHAT` (激活闲聊)**:
        - **触发条件**: `Heartflow` 的主状态 (`MaiState`) 允许 `CHAT` 模式，且当前 `CHAT` 状态的子心流数量未达上限。
        - **判定机制**: `SubHeartflowManager` 中的 `sbhf_absent_into_chat` 方法调用大模型(LLM)。LLM 读取该群聊的近期内容和结合自身个性信息，判断是否"想"在该群开始聊天。
        - **执行**: 若 LLM 判断为是，且名额未满，`SubHeartflowManager` 调用 `change_chat_state(ChatState.CHAT)`。
    - **`CHAT` -> `FOCUSED` (激活专注)**:
        - **触发条件**: 子心流处于 `CHAT` 状态，其内部维护的"开屎热聊"概率 (`InterestChatting.start_hfc_probability`) 达到预设阈值（表示对当前聊天兴趣浓厚），同时 `Heartflow` 的主状态允许 `FOCUSED` 模式，且 `FOCUSED` 名额未满。
        - **判定机制**: `SubHeartflowManager` 中的 `sbhf_absent_into_focus` 方法定期检查满足条件的 `CHAT` 子心流。
        - **执行**: 若满足所有条件，`SubHeartflowManager` 调用 `change_chat_state(ChatState.FOCUSED)`。
        - **注意**: 无法从 `ABSENT` 直接跳到 `FOCUSED`，必须先经过 `CHAT`。
    - **`FOCUSED` -> `ABSENT` (退出专注)**:
        - **主要途径 (内部驱动)**: 在 `FOCUSED` 状态下运行的 `HeartFlowChatInstance` 连续多次决策为 `no_reply` (例如达到 5 次，次数可配)，它会通过回调函数 (`sbhf_focus_into_absent`) 请求 `SubHeartflowManager` 将其状态**直接**设置为 `ABSENT`。
        - **其他途径 (外部驱动)**:
            - `Heartflow` 主状态变为 `OFFLINE`，`SubHeartflowManager` 强制所有子心流变为 `ABSENT`。
            - `SubHeartflowManager` 因 `FOCUSED` 名额超限 (`enforce_subheartflow_limits`) 或随机停用 (`randomly_deactivate_subflows`) 而将其设置为 `ABSENT`。
    - **`CHAT` -> `ABSENT` (退出闲聊)**:
        - **主要途径 (内部驱动)**: `SubHeartflowManager` 中的 `sbhf_absent_into_chat` 方法调用 LLM。LLM 读取群聊内容和结合自身状态，判断是否"不想"继续在此群闲聊。
        - **执行**: 若 LLM 判断为是，`SubHeartflowManager` 调用 `change_chat_state(ChatState.ABSENT)`。
        - **其他途径 (外部驱动)**:
            - `Heartflow` 主状态变为 `OFFLINE`。
            - `SubHeartflowManager` 因 `CHAT` 名额超限或随机停用。
    - **全局强制 `ABSENT`**: 当 `Heartflow` 的 `MaiState` 变为 `OFFLINE` 时，`SubHeartflowManager` 会调用所有子心流的 `change_chat_state(ChatState.ABSENT)`，强制它们全部停止活动。
    - **状态变更执行者**: `change_chat_state` 方法仅负责执行状态的切换和对应聊天实例的启停，不进行名额检查。名额检查的责任由 `SubHeartflowManager` 中的各个决策方法承担。
    - **最终清理**: 进入 `ABSENT` 状态的子心流不会立即被删除，只有在 `ABSENT` 状态持续一小时 (`INACTIVE_THRESHOLD_SECONDS`) 后，才会被后台清理任务 (`cleanup_inactive_subheartflows`) 删除。

## 3. 聊天实例详解 (Chat Instances Explained)

### 3.1. NormalChatInstance
- **激活条件**: 对应 `SubHeartflow` 的 `ChatState` 为 `CHAT`。
- **工作流程**:
    - 当 `SubHeartflow` 进入 `CHAT` 状态时，`NormalChatInstance` 会被激活。
    - 实例启动后，会创建一个后台任务 (`_reply_interested_message`)。
    - 该任务持续监控由 `InterestChatting` 传入的、具有一定兴趣度的消息列表 (`interest_dict`)。
    - 对列表中的每条消息，结合是否被提及 (`@`)、消息本身的兴趣度以及当前的回复意愿 (`WillingManager`)，计算出一个回复概率。
    - 根据计算出的概率随机决定是否对该消息进行回复。
    - 如果决定回复，则调用 `NormalChatGenerator` 生成回复内容，并可能附带表情包。
- **行为特点**:
    - 回复相对常规、简单。
    - 不投入过多计算资源。
    - 侧重于维持基本的交流氛围。
    - 示例：对问候语、日常分享等进行简单回应。

### 3.2. HeartFlowChatInstance (继承自原 PFC 逻辑)
- **激活条件**: 对应 `SubHeartflow` 的 `ChatState` 为 `FOCUSED`。
- **工作流程**:
    - 基于更复杂的规则（原 PFC 模式）进行深度处理。
    - 对群内话题进行深入分析。
    - 可能主动发起相关话题或引导交流。
- **行为特点**:
    - 回复更积极、深入。
    - 投入更多资源参与聊天。
    - 回复内容可能更详细、有针对性。
    - 对话题参与度高，能带动交流。
    - 示例：对复杂或有争议话题阐述观点，并与人互动。

## 4. 工作流程示例 (Example Workflow)

1.  **启动**: `Heartflow` 启动，初始化 `MaiStateInfo` (例如 `OFFLINE`) 和 `SubHeartflowManager`。
2.  **状态变化**: 用户操作或内部逻辑使 `Heartflow` 的 `current_state` 变为 `NORMAL_CHAT`。
3.  **管理器响应**: `SubHeartflowManager` 检测到状态变化，根据 `NORMAL_CHAT` 的限制，调用 `get_or_create_subheartflow` 获取或创建子心流，并通过 `change_chat_state` 将部分子心流状态从 `ABSENT` 激活为 `CHAT`。
4.  **子心流激活**: 被激活的 `SubHeartflow` 启动其 `NormalChatInstance`。
5.  **信息接收**: 该 `SubHeartflow` 的 `ChattingObservation` 开始从数据库拉取新消息。
6.  **普通回复**: `NormalChatInstance` 处理观察到的信息，执行普通回复逻辑。
7.  **兴趣评估**: `SubHeartflowManager` 定期评估该子心流的 `InterestChatting` 状态。
8.  **提升状态**: 若兴趣度达标且 `Heartflow` 状态允许，`SubHeartflowManager` 调用该子心流的 `change_chat_state` 将其状态提升为 `FOCUSED`。
9.  **子心流切换**: `SubHeartflow` 内部停止 `NormalChatInstance`，启动 `HeartFlowChatInstance`。
10. **专注回复**: `HeartFlowChatInstance` 开始根据其逻辑进行更深入的交互。
11. **状态回落/停用**: 若 `Heartflow` 状态变为 `OFFLINE`，`SubHeartflowManager` 会调用所有活跃子心流的 `change_chat_state(ChatState.ABSENT)`，使其进入 `ABSENT` 状态（它们不会立即被删除，只有在 `ABSENT` 状态持续1小时后才会被清理）。

## 5. 使用与配置 (Usage and Configuration)

### 5.1. 使用说明 (Code Examples)
- **(内部)创建/获取子心流** (由 `SubHeartflowManager` 调用, 示例):
  ```python
  # subheartflow_manager.py (get_or_create_subheartflow 内部)
  # 注意：mai_states 现在是 self.mai_state_info
  new_subflow = SubHeartflow(subheartflow_id, self.mai_state_info)
  await new_subflow.initialize()
  observation = ChattingObservation(chat_id=subheartflow_id)
  new_subflow.add_observation(observation)
  ```
- **(内部)添加观察者** (由 `SubHeartflowManager` 或 `SubHeartflow` 内部调用):
  ```python
  # sub_heartflow.py
  self.observations.append(observation)
  ```

