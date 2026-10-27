import tkinter as tk
from tkinter import ttk
import time
import os
from datetime import datetime, timedelta
import random
from collections import deque
import json  # 引入 json

# --- 引入 Matplotlib ---
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates  # 用于处理日期格式
import matplotlib  # 导入 matplotlib

# --- 配置 ---
LOG_FILE_PATH = os.path.join("logs", "interest", "interest_history.log")  # 指向历史日志文件
REFRESH_INTERVAL_MS = 200  # 刷新间隔 (毫秒) - 可以适当调长，因为读取文件可能耗时
WINDOW_TITLE = "Interest Monitor (Live History)"
MAX_HISTORY_POINTS = 1000  # 图表上显示的最大历史点数 (可以增加)
MAX_STREAMS_TO_DISPLAY = 15  # 最多显示多少个聊天流的折线图 (可以增加)
MAX_QUEUE_SIZE = 30  # 新增：历史想法队列最大长度

# *** 添加 Matplotlib 中文字体配置 ***
# 尝试使用 'SimHei' 或 'Microsoft YaHei'，如果找不到，matplotlib 会回退到默认字体
# 确保你的系统上安装了这些字体
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei"]
matplotlib.rcParams["axes.unicode_minus"] = False  # 解决负号'-'显示为方块的问题


class InterestMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1800x800")  # 调整窗口大小以适应图表

        # --- 数据存储 ---
        # 使用 deque 来存储有限的历史数据点
        # key: stream_id, value: deque([(timestamp, interest_level), ...])
        self.stream_history = {}
        # key: stream_id, value: deque([(timestamp, reply_probability), ...])
        self.probability_history = {}
        self.stream_colors = {}  # 为每个 stream 分配颜色
        self.stream_display_names = {}  # 存储显示名称 (group_name)
        self.selected_stream_id = tk.StringVar()  # 用于 Combobox 绑定

        # --- 新增：存储其他参数 ---
        # 顶层信息
        self.latest_main_mind = tk.StringVar(value="N/A")
        self.latest_mai_state = tk.StringVar(value="N/A")
        self.latest_subflow_count = tk.IntVar(value=0)
        # 子流最新状态 (key: stream_id)
        self.stream_sub_minds = {}
        self.stream_chat_states = {}
        self.stream_threshold_status = {}
        self.stream_last_active = {}
        self.stream_last_interaction = {}
        # 用于显示单个流详情的 StringVar
        self.single_stream_sub_mind = tk.StringVar(value="想法: N/A")
        self.single_stream_chat_state = tk.StringVar(value="状态: N/A")
        self.single_stream_threshold = tk.StringVar(value="阈值: N/A")
        self.single_stream_last_active = tk.StringVar(value="活跃: N/A")
        self.single_stream_last_interaction = tk.StringVar(value="交互: N/A")

        # 新增：历史想法队列
        self.main_mind_history = deque(maxlen=MAX_QUEUE_SIZE)
        self.last_main_mind_timestamp = 0  # 记录最后一条main_mind的时间戳

        # --- UI 元素 ---

        # --- 新增：顶部全局信息框架 ---
        self.global_info_frame = ttk.Frame(root, padding="5 0 5 5")  # 顶部内边距调整
        self.global_info_frame.pack(side=tk.TOP, fill=tk.X, pady=(5, 0))  # 底部外边距为0

        ttk.Label(self.global_info_frame, text="全局状态:").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(self.global_info_frame, textvariable=self.latest_mai_state).pack(side=tk.LEFT, padx=5)
        ttk.Label(self.global_info_frame, text="想法:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(self.global_info_frame, textvariable=self.latest_main_mind).pack(side=tk.LEFT, padx=5)
        ttk.Label(self.global_info_frame, text="子流数:").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(self.global_info_frame, textvariable=self.latest_subflow_count).pack(side=tk.LEFT, padx=5)

        # 创建 Notebook (选项卡控件)
        self.notebook = ttk.Notebook(root)
        # 修改：fill 和 expand，让 notebook 填充剩余空间
        self.notebook.pack(pady=(5, 0), padx=10, fill=tk.BOTH, expand=1)  # 顶部外边距改小

        # --- 第一个选项卡：所有流 ---
        self.frame_all = ttk.Frame(self.notebook, padding="5 5 5 5")
        self.notebook.add(self.frame_all, text="所有聊天流")

        # 状态标签 (移动到最底部)
        self.status_label = tk.Label(root, text="Initializing...", anchor="w", fg="grey")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 5))  # 调整边距

        # Matplotlib 图表设置 (用于第一个选项卡)
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        # 配置在 update_plot 中进行，避免重复

        # 创建 Tkinter 画布嵌入 Matplotlib 图表 (用于第一个选项卡)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame_all)  # <--- 放入 frame_all
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=1)

        # --- 第二个选项卡：单个流 ---
        self.frame_single = ttk.Frame(self.notebook, padding="5 5 5 5")
        self.notebook.add(self.frame_single, text="单个聊天流详情")

        # 单个流选项卡的上部控制区域
        self.control_frame_single = ttk.Frame(self.frame_single)
        self.control_frame_single.pack(side=tk.TOP, fill=tk.X, pady=5)

        ttk.Label(self.control_frame_single, text="选择聊天流:").pack(side=tk.LEFT, padx=(0, 5))
        self.stream_selector = ttk.Combobox(
            self.control_frame_single, textvariable=self.selected_stream_id, state="readonly", width=50
        )
        self.stream_selector.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.stream_selector.bind("<<ComboboxSelected>>", self.on_stream_selected)

        # --- 新增：单个流详情显示区域 ---
        self.single_stream_details_frame = ttk.Frame(self.frame_single, padding="5 5 5 0")
        self.single_stream_details_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        ttk.Label(self.single_stream_details_frame, textvariable=self.single_stream_sub_mind).pack(side=tk.LEFT, padx=5)
        ttk.Label(self.single_stream_details_frame, textvariable=self.single_stream_chat_state).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(self.single_stream_details_frame, textvariable=self.single_stream_threshold).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(self.single_stream_details_frame, textvariable=self.single_stream_last_active).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(self.single_stream_details_frame, textvariable=self.single_stream_last_interaction).pack(
            side=tk.LEFT, padx=5
        )

        # Matplotlib 图表设置 (用于第二个选项卡)
        self.fig_single = Figure(figsize=(5, 4), dpi=100)
        # 修改：创建两个子图，一个显示兴趣度，一个显示概率
        self.ax_single_interest = self.fig_single.add_subplot(211)  # 2行1列的第1个
        self.ax_single_probability = self.fig_single.add_subplot(
            212, sharex=self.ax_single_interest
        )  # 2行1列的第2个，共享X轴

        # 创建 Tkinter 画布嵌入 Matplotlib 图表 (用于第二个选项卡)
        self.canvas_single = FigureCanvasTkAgg(self.fig_single, master=self.frame_single)  # <--- 放入 frame_single
        self.canvas_widget_single = self.canvas_single.get_tk_widget()
        self.canvas_widget_single.pack(side=tk.TOP, fill=tk.BOTH, expand=1)

        # --- 新增第三个选项卡：Moui历史想法 ---
        self.frame_mind_history = ttk.Frame(self.notebook, padding="5 5 5 5")
        self.notebook.add(self.frame_mind_history, text="Moui历史想法")

        # 聊天框样式的文本框（只读）+ 滚动条
        self.mind_text_scroll = tk.Scrollbar(self.frame_mind_history)
        self.mind_text_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.mind_text = tk.Text(
            self.frame_mind_history,
            height=25,
            state="disabled",
            wrap="word",
            font=("微软雅黑", 12),
            yscrollcommand=self.mind_text_scroll.set,
        )
        self.mind_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=1, padx=5, pady=5)
        self.mind_text_scroll.config(command=self.mind_text.yview)

        # --- 初始化和启动刷新 ---
        self.update_display()  # 首次加载并开始刷新循环

    def on_stream_selected(self, event=None):
        """当 Combobox 选择改变时调用，更新单个流的图表"""
        self.update_single_stream_plot()

    def get_random_color(self):
        """生成随机颜色用于区分线条"""
        return "#{:06x}".format(random.randint(0, 0xFFFFFF))

    def load_main_mind_history(self):
        """只读取包含main_mind的日志行，维护历史想法队列"""
        if not os.path.exists(LOG_FILE_PATH):
            return

        main_mind_entries = []
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        log_entry = json.loads(line.strip())
                        if "main_mind" in log_entry:
                            ts = log_entry.get("timestamp", 0)
                            main_mind_entries.append((ts, log_entry))
                    except Exception:
                        continue
            main_mind_entries.sort(key=lambda x: x[0])
            recent_entries = main_mind_entries[-MAX_QUEUE_SIZE:]
            self.main_mind_history.clear()
            for _ts, entry in recent_entries:
                self.main_mind_history.append(entry)
            if recent_entries:
                self.last_main_mind_timestamp = recent_entries[-1][0]
            # 首次加载时刷新
            self.refresh_mind_text()
        except Exception:
            pass

    def update_main_mind_history(self):
        """实时监控log文件，发现新main_mind数据则更新队列和展示（仅有新数据时刷新）"""
        if not os.path.exists(LOG_FILE_PATH):
            return

        new_entries = []
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                for line in reversed(list(f)):
                    try:
                        log_entry = json.loads(line.strip())
                        if "main_mind" in log_entry:
                            ts = log_entry.get("timestamp", 0)
                            if ts > self.last_main_mind_timestamp:
                                new_entries.append((ts, log_entry))
                            else:
                                break
                    except Exception:
                        continue
            if new_entries:
                for ts, entry in sorted(new_entries):
                    if len(self.main_mind_history) >= MAX_QUEUE_SIZE:
                        self.main_mind_history.popleft()
                    self.main_mind_history.append(entry)
                    self.last_main_mind_timestamp = ts
                self.refresh_mind_text()  # 只有有新数据时才刷新
        except Exception:
            pass

    def refresh_mind_text(self):
        """刷新聊天框样式的历史想法展示"""
        self.mind_text.config(state="normal")
        self.mind_text.delete(1.0, tk.END)
        for entry in self.main_mind_history:
            ts = entry.get("timestamp", 0)
            dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            main_mind = entry.get("main_mind", "")
            mai_state = entry.get("mai_state", "")
            subflow_count = entry.get("subflow_count", "")
            msg = f"[{dt_str}] 状态:{mai_state} 子流:{subflow_count}\n{main_mind}\n\n"
            self.mind_text.insert(tk.END, msg)
        self.mind_text.see(tk.END)
        self.mind_text.config(state="disabled")

    def load_and_update_history(self):
        """从 history log 文件加载数据并更新历史记录"""
        if not os.path.exists(LOG_FILE_PATH):
            self.set_status(f"Error: Log file not found at {LOG_FILE_PATH}", "red")
            # 如果文件不存在，不清空现有数据，以便显示最后一次成功读取的状态
            return

        # *** Reset display names each time we reload ***
        new_stream_history = {}
        new_stream_display_names = {}
        new_probability_history = {}  # <--- 重置概率历史
        # --- 新增：重置其他子流状态 --- (如果需要的话，但通常覆盖即可)
        # self.stream_sub_minds = {}
        # self.stream_chat_states = {}
        # ... 等等 ...

        read_count = 0
        error_count = 0
        # *** Calculate the timestamp threshold for the last 30 minutes ***
        current_time = time.time()
        time_threshold = current_time - (15 * 60)  # 30 minutes in seconds

        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    read_count += 1
                    try:
                        log_entry = json.loads(line.strip())
                        timestamp = log_entry.get("timestamp")  # 获取顶层时间戳

                        # *** 时间过滤 ***
                        if timestamp is None:
                            error_count += 1
                            continue  # 跳过没有时间戳的行
                        try:
                            entry_timestamp = float(timestamp)
                            if entry_timestamp < time_threshold:
                                continue  # 跳过时间过早的条目
                        except (ValueError, TypeError):
                            error_count += 1
                            continue  # 跳过时间戳格式错误的行

                        # --- 新增：更新顶层信息 (使用最后一个有效行的数据) ---
                        self.latest_main_mind.set(
                            log_entry.get("main_mind", self.latest_main_mind.get())
                        )  # 保留旧值如果缺失
                        self.latest_mai_state.set(log_entry.get("mai_state", self.latest_mai_state.get()))
                        self.latest_subflow_count.set(log_entry.get("subflow_count", self.latest_subflow_count.get()))

                        # --- 修改开始：迭代 subflows ---
                        subflows = log_entry.get("subflows")
                        if not isinstance(subflows, list):  # 检查 subflows 是否存在且为列表
                            error_count += 1
                            continue  # 跳过没有 subflows 或格式无效的行

                        for subflow_entry in subflows:
                            stream_id = subflow_entry.get("stream_id")
                            interest_level = subflow_entry.get("interest_level")
                            # 获取 group_name，如果不存在则回退到 stream_id
                            group_name = subflow_entry.get("group_name", stream_id)
                            # reply_probability = subflow_entry.get("reply_probability")  # 获取概率值 # <-- 注释掉旧行
                            start_hfc_probability = subflow_entry.get(
                                "start_hfc_probability"
                            )  # <-- 添加新行，读取新字段

                            # *** 检查必要的字段 ***
                            # 注意：时间戳已在顶层检查过
                            if stream_id is None or interest_level is None:
                                # 这里可以选择记录子流错误，但暂时跳过
                                continue  # 跳过无效的 subflow 条目

                            # 确保 interest_level 可以转换为浮点数
                            try:
                                interest_level_float = float(interest_level)
                            except (ValueError, TypeError):
                                continue  # 跳过 interest_level 无效的 subflow

                            # 如果是第一次读到这个 stream_id，则创建 deque
                            if stream_id not in new_stream_history:
                                new_stream_history[stream_id] = deque(maxlen=MAX_HISTORY_POINTS)
                                new_probability_history[stream_id] = deque(maxlen=MAX_HISTORY_POINTS)  # 创建概率 deque
                                # 检查是否已有颜色，没有则分配
                                if stream_id not in self.stream_colors:
                                    self.stream_colors[stream_id] = self.get_random_color()

                            # *** 存储此 stream_id 最新的显示名称 ***
                            new_stream_display_names[stream_id] = group_name

                            # --- 新增：存储其他子流信息 ---
                            self.stream_sub_minds[stream_id] = subflow_entry.get("sub_mind", "N/A")
                            self.stream_chat_states[stream_id] = subflow_entry.get("sub_chat_state", "N/A")
                            self.stream_threshold_status[stream_id] = subflow_entry.get("is_above_threshold", False)
                            self.stream_last_active[stream_id] = subflow_entry.get(
                                "chat_state_changed_time"
                            )  # 存储原始时间戳

                            # 添加数据点 (使用顶层时间戳)
                            new_stream_history[stream_id].append((entry_timestamp, interest_level_float))

                            # 添加概率数据点 (如果存在且有效)
                            # if reply_probability is not None: # <-- 注释掉旧判断
                            if start_hfc_probability is not None:  # <-- 修改判断条件
                                try:
                                    # 尝试将概率转换为浮点数
                                    # probability_float = float(reply_probability) # <-- 注释掉旧转换
                                    probability_float = float(start_hfc_probability)  # <-- 使用新变量
                                    new_probability_history[stream_id].append((entry_timestamp, probability_float))
                                except (TypeError, ValueError):
                                    # 如果概率值无效，可以跳过或记录一个默认值，这里跳过
                                    pass
                        # --- 修改结束 ---

                    except json.JSONDecodeError:
                        error_count += 1
                        # logger.warning(f"Skipping invalid JSON line: {line.strip()}")
                        continue  # 跳过无法解析的行
                    # except (TypeError, ValueError) as e: # 这个外层 catch 可能不再需要，因为类型错误在内部处理了
                    #     error_count += 1
                    #     # logger.warning(f"Skipping line due to data type error ({e}): {line.strip()}")
                    #     continue  # 跳过数据类型错误的行

            # 读取完成后，用新数据替换旧数据
            self.stream_history = new_stream_history
            self.stream_display_names = new_stream_display_names  # *** Update display names ***
            self.probability_history = new_probability_history  # <--- 更新概率历史
            # 清理不再存在的 stream_id 的附加信息 (可选，但保持一致性)
            streams_to_remove = set(self.stream_sub_minds.keys()) - set(new_stream_history.keys())
            for sid in streams_to_remove:
                self.stream_sub_minds.pop(sid, None)
                self.stream_chat_states.pop(sid, None)
                self.stream_threshold_status.pop(sid, None)
                self.stream_last_active.pop(sid, None)
                self.stream_last_interaction.pop(sid, None)
                # 颜色和显示名称也应该清理，但当前逻辑是保留旧颜色
                # self.stream_colors.pop(sid, None)
            status_msg = f"Data loaded at {datetime.now().strftime('%H:%M:%S')}. Lines read: {read_count}."
            if error_count > 0:
                status_msg += f" Skipped {error_count} invalid lines."
                self.set_status(status_msg, "orange")
            else:
                self.set_status(status_msg, "green")

        except IOError as e:
            self.set_status(f"Error reading file {LOG_FILE_PATH}: {e}", "red")
        except Exception as e:
            self.set_status(f"An unexpected error occurred during loading: {e}", "red")

        # --- 更新 Combobox ---
        self.update_stream_selector()

    def update_stream_selector(self):
        """更新单个流选项卡中的 Combobox 列表"""
        # 创建 (display_name, stream_id) 对的列表，按 display_name 排序
        available_streams = sorted(
            [
                (name, sid)
                for sid, name in self.stream_display_names.items()
                if sid in self.stream_history and self.stream_history[sid]
            ],
            key=lambda item: item[0],  # 按显示名称排序
        )

        # 更新 Combobox 的值 (仅显示 display_name)
        self.stream_selector["values"] = [name for name, sid in available_streams]

        # 检查当前选中的 stream_id 是否仍然有效
        current_selection_name = self.selected_stream_id.get()
        current_selection_valid = any(name == current_selection_name for name, sid in available_streams)

        if not current_selection_valid and available_streams:
            # 如果当前选择无效，并且有可选流，则默认选中第一个
            self.selected_stream_id.set(available_streams[0][0])
            # 手动触发一次更新，因为 set 不会触发 <<ComboboxSelected>>
            self.update_single_stream_plot()
        elif not available_streams:
            # 如果没有可选流，清空选择
            self.selected_stream_id.set("")
            self.update_single_stream_plot()  # 清空图表

    def update_all_streams_plot(self):
        """更新第一个选项卡的 Matplotlib 图表 (显示所有流)"""
        self.ax.clear()  # 清除旧图
        # *** 设置中文标题和标签 ***
        self.ax.set_title("兴趣度随时间变化图 (所有活跃流)")
        self.ax.set_xlabel("时间")
        self.ax.set_ylabel("兴趣度")
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        self.ax.grid(True)
        self.ax.set_ylim(0, 10)  # 固定 Y 轴范围 0-10

        # 只绘制最新的 N 个 stream (按最后记录的兴趣度排序)
        # 注意：现在是基于文件读取的快照排序，可能不是实时最新
        active_streams = sorted(
            self.stream_history.items(),
            key=lambda item: item[1][-1][1] if item[1] else 0,  # 按最后兴趣度排序
            reverse=True,
        )[:MAX_STREAMS_TO_DISPLAY]

        all_times = []  # 用于确定 X 轴范围

        for stream_id, history in active_streams:
            if not history:
                continue

            timestamps, interests = zip(*history)
            # 将 time.time() 时间戳转换为 matplotlib 可识别的日期格式
            try:
                mpl_dates = [datetime.fromtimestamp(ts) for ts in timestamps]
                all_times.extend(mpl_dates)  # 收集所有时间点

                # *** Use display name for label ***
                display_label = self.stream_display_names.get(stream_id, stream_id)

                self.ax.plot(
                    mpl_dates,
                    interests,
                    label=display_label,  # *** Use display_label ***
                    color=self.stream_colors.get(stream_id, "grey"),
                    marker=".",
                    markersize=3,
                    linestyle="-",
                    linewidth=1,
                )
            except ValueError as e:
                print(f"Skipping plot for {stream_id} due to invalid timestamp: {e}")
                continue

        if all_times:
            # 根据数据动态调整 X 轴范围，留一点边距
            min_time = min(all_times)
            max_time = max(all_times)
            # delta = max_time - min_time
            # self.ax.set_xlim(min_time - delta * 0.05, max_time + delta * 0.05)
            self.ax.set_xlim(min_time, max_time)

            # 自动格式化X轴标签
            self.fig.autofmt_xdate()
        else:
            # 如果没有数据，设置一个默认的时间范围，例如最近一小时
            now = datetime.now()
            one_hour_ago = now - timedelta(hours=1)
            self.ax.set_xlim(one_hour_ago, now)

        # 添加图例
        if active_streams:
            # 调整图例位置和大小
            # 字体已通过全局 matplotlib.rcParams 设置
            self.ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0.0, fontsize="x-small")
            # 调整布局，确保图例不被裁剪
            self.fig.tight_layout(rect=[0, 0, 0.85, 1])  # 右侧留出空间给图例

        self.canvas.draw()  # 重绘画布

    def update_single_stream_plot(self):
        """更新第二个选项卡的 Matplotlib 图表 (显示单个选定的流)"""
        self.ax_single_interest.clear()
        self.ax_single_probability.clear()

        # 设置子图标题和标签
        self.ax_single_interest.set_title("兴趣度")
        self.ax_single_interest.set_ylim(0, 10)  # 固定 Y 轴范围 0-10

        # self.ax_single_probability.set_title("回复评估概率") # <-- 注释掉旧标题
        self.ax_single_probability.set_title("HFC 启动概率")  # <-- 修改标题
        self.ax_single_probability.set_xlabel("时间")
        # self.ax_single_probability.set_ylabel("概率") # <-- 注释掉旧标签
        self.ax_single_probability.set_ylabel("HFC 概率")  # <-- 修改 Y 轴标签
        self.ax_single_probability.grid(True)
        self.ax_single_probability.set_ylim(0, 1.05)  # 固定 Y 轴范围 0-1
        self.ax_single_probability.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

        selected_name = self.selected_stream_id.get()
        selected_sid = None

        # --- 新增：根据选中的名称找到 stream_id ---
        if selected_name:
            for sid, name in self.stream_display_names.items():
                if name == selected_name:
                    selected_sid = sid
                    break

        all_times = []  # 用于确定 X 轴范围

        # --- 新增：绘制兴趣度图 ---
        if selected_sid and selected_sid in self.stream_history and self.stream_history[selected_sid]:
            history = self.stream_history[selected_sid]
            timestamps, interests = zip(*history)
            try:
                mpl_dates = [datetime.fromtimestamp(ts) for ts in timestamps]
                all_times.extend(mpl_dates)
                self.ax_single_interest.plot(
                    mpl_dates,
                    interests,
                    color=self.stream_colors.get(selected_sid, "blue"),
                    marker=".",
                    markersize=3,
                    linestyle="-",
                    linewidth=1,
                )
            except ValueError as e:
                print(f"Skipping interest plot for {selected_sid} due to invalid timestamp: {e}")

        # --- 新增：绘制概率图 ---
        if selected_sid and selected_sid in self.probability_history and self.probability_history[selected_sid]:
            prob_history = self.probability_history[selected_sid]
            prob_timestamps, probabilities = zip(*prob_history)
            try:
                prob_mpl_dates = [datetime.fromtimestamp(ts) for ts in prob_timestamps]
                # 注意：概率图的时间点可能与兴趣度不同，也需要加入 all_times
                all_times.extend(prob_mpl_dates)
                self.ax_single_probability.plot(
                    prob_mpl_dates,
                    probabilities,
                    color=self.stream_colors.get(selected_sid, "green"),  # 可以用不同颜色
                    marker=".",
                    markersize=3,
                    linestyle="-",
                    linewidth=1,
                )
            except ValueError as e:
                print(f"Skipping probability plot for {selected_sid} due to invalid timestamp: {e}")

        # --- 新增：调整 X 轴范围和格式 ---
        if all_times:
            min_time = min(all_times)
            max_time = max(all_times)
            # 设置共享的 X 轴范围
            self.ax_single_interest.set_xlim(min_time, max_time)
            # self.ax_single_probability.set_xlim(min_time, max_time) # sharex 会自动同步
            # 自动格式化X轴标签 (应用到共享轴的最后一个子图上通常即可)
            self.fig_single.autofmt_xdate()
        else:
            # 如果没有数据，设置一个默认的时间范围
            now = datetime.now()
            one_hour_ago = now - timedelta(hours=1)
            self.ax_single_interest.set_xlim(one_hour_ago, now)
            # self.ax_single_probability.set_xlim(one_hour_ago, now) # sharex 会自动同步

        # --- 新增：更新单个流的详细信息标签 ---
        self.update_single_stream_details(selected_sid)

        # --- 新增：重新绘制画布 ---
        self.canvas_single.draw()

    def format_timestamp(self, ts):
        """辅助函数：格式化时间戳，处理 None 或无效值"""
        if ts is None:
            return "N/A"
        try:
            # 假设 ts 是 float 类型的时间戳
            dt_object = datetime.fromtimestamp(float(ts))
            return dt_object.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return "Invalid Time"

    def update_single_stream_details(self, stream_id):
        """更新单个流详情区域的标签内容"""
        if stream_id:
            sub_mind = self.stream_sub_minds.get(stream_id, "N/A")
            chat_state = self.stream_chat_states.get(stream_id, "N/A")
            threshold = self.stream_threshold_status.get(stream_id, False)
            last_active_ts = self.stream_last_active.get(stream_id)
            last_interaction_ts = self.stream_last_interaction.get(stream_id)

            self.single_stream_sub_mind.set(f"想法: {sub_mind}")
            self.single_stream_chat_state.set(f"状态: {chat_state}")
            self.single_stream_threshold.set(f"阈值以上: {'是' if threshold else '否'}")
            self.single_stream_last_active.set(f"最后活跃: {self.format_timestamp(last_active_ts)}")
            self.single_stream_last_interaction.set(f"最后交互: {self.format_timestamp(last_interaction_ts)}")
        else:
            # 如果没有选择流，则清空详情
            self.single_stream_sub_mind.set("想法: N/A")
            self.single_stream_chat_state.set("状态: N/A")
            self.single_stream_threshold.set("阈值: N/A")
            self.single_stream_last_active.set("活跃: N/A")
            self.single_stream_last_interaction.set("交互: N/A")

    def update_display(self):
        """主更新循环"""
        try:
            # --- 新增：首次加载历史想法 ---
            if not hasattr(self, "_main_mind_loaded"):
                self.load_main_mind_history()
                self._main_mind_loaded = True
            else:
                self.update_main_mind_history()  # 只有有新main_mind数据时才刷新界面
            # *** 修改：分别调用两个图表的更新方法 ***
            self.load_and_update_history()  # 从文件加载数据并更新内部状态
            self.update_all_streams_plot()  # 更新所有流的图表
            self.update_single_stream_plot()  # 更新单个流的图表
        except Exception as e:
            # 提供更详细的错误信息
            import traceback

            error_msg = f"Error during update: {e}\n{traceback.format_exc()}"
            self.set_status(error_msg, "red")
            print(error_msg)  # 打印详细错误到控制台

        # 安排下一次刷新
        self.root.after(REFRESH_INTERVAL_MS, self.update_display)

    def set_status(self, message: str, color: str = "grey"):
        """更新状态栏标签"""
        # 限制状态栏消息长度
        max_len = 150
        display_message = (message[:max_len] + "...") if len(message) > max_len else message
        self.status_label.config(text=display_message, fg=color)


if __name__ == "__main__":
    # 导入 timedelta 用于默认时间范围
    from datetime import timedelta

    root = tk.Tk()
    app = InterestMonitorApp(root)
    root.mainloop()
