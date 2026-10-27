import os
import shutil
import tomli
import tomli_w
from dotenv import dotenv_values
from loguru import logger

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "bot_config.toml")
BACKUP_PATH = CONFIG_PATH + ".bak"
LPMM_CONFIG_PATH = os.path.join(BASE_DIR, "config", "lpmm_config.toml")
LPMM_BACKUP_PATH = LPMM_CONFIG_PATH + ".bak"

def backup_config():
    try:
        if not os.path.exists(BACKUP_PATH) and os.path.exists(CONFIG_PATH):
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            shutil.copy(CONFIG_PATH, BACKUP_PATH)
            logger.info(f"已创建配置文件备份：{BACKUP_PATH}")
        else:
            logger.info("主配置备份文件已存在，跳过备份")
        # 新增lpmm_config.toml的备份
        if os.path.exists(LPMM_CONFIG_PATH) and not os.path.exists(LPMM_BACKUP_PATH):
            os.makedirs(os.path.dirname(LPMM_CONFIG_PATH), exist_ok=True)
            shutil.copy(LPMM_CONFIG_PATH, LPMM_BACKUP_PATH)
            logger.info(f"已创建lpmm配置文件备份：{LPMM_BACKUP_PATH}")
        elif os.path.exists(LPMM_BACKUP_PATH):
            logger.info("lpmm配置备份文件已存在，跳过备份")
    except Exception as e:
        logger.error(f"备份失败: {str(e)}")
        raise

def load_config():
    default_config = {
        'bot': {'nickname': 'Moui'},
        'personality': {'personality_core': '', 'personality_sides': []},
        'groups': {'talk_allowed': []},
        'identity': {
            'identity_detail': [], 
            'height': 170,
            'weight': 50,
            'age': 20,
            'gender': '男',
            'appearance': ''
        },
        'experimental': {'enable_friend_chat': False, 'pfc_chatting': False}
    }
    try:
        if not os.path.exists(CONFIG_PATH):
            logger.warning("未找到配置文件，使用默认配置")
            return default_config
        with open(CONFIG_PATH, "rb") as f:
            user_config = tomli.load(f)
            def deep_merge(source, overrides):
                for key, value in overrides.items():
                    if isinstance(value, dict):
                        source[key] = deep_merge(source.get(key, {}), value)
                    else:
                        source[key] = value
                return source
            return deep_merge(default_config, user_config)
    except tomli.TOMLDecodeError as e:
        logger.error(f"配置文件解析失败: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"读取配置失败: {str(e)}")
        raise

def save_config(config):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "wb") as f:
            tomli_w.dump(config, f)
        logger.success("配置文件已保存")
    except Exception as e:
        logger.error(f"保存配置失败: {str(e)}")
        raise

def step_bot(config):
    print("\n=== 配置昵称 ===")
    try:
        nickname = input(f"请输入机器人的昵称（当前：{config['bot']['nickname']}）：").strip()
        if nickname.replace(" ", "") != "":
            config['bot']['nickname'] = nickname
    except Exception as e:
        logger.error(f"昵称配置异常: {str(e)}")
    return config

def step_groups(config):
    print("\n=== 配置可发消息群聊 ===")
    groups = []
    while True:
        try:
            group = input(f"请输入第{len(groups)+1}个群号（留空结束）：").strip()
            if not group or group.isspace():
                break
            if group.isdigit():
                groups.append(int(group))
            else:
                logger.warning("错误：请输入有效的数字群号！")
        except ValueError:
            logger.warning("错误：无效的数字格式！")
        except KeyboardInterrupt:
            print("\n输入已取消")
            break
        except Exception as e:
            logger.error(f"群聊配置异常: {str(e)}")
            break
    if groups:
        config['groups']['talk_allowed'] = groups
    return config

def step_personality(config):
    print("\n=== 配置人格 ===")
    try:
        core = input(f"请输入主人格描述（当前：{config['personality']['personality_core']}）：").strip()
        if core.replace(" ", "") != "":
            config['personality']['personality_core'] = core
        print("\n--- 配置人格细节 ---")
        sides = []
        while True:
            try:
                side = input(f"请输入第{len(sides)+1}条人格细节（留空结束）：").strip()
                if not side or side.isspace():
                    break
                sides.append(side)
            except KeyboardInterrupt:
                print("\n输入已取消")
                break
            except Exception as e:
                logger.error(f"人格细节配置异常: {str(e)}")
                break
        if sides:
            config['personality']['personality_sides'] = sides
    except Exception as e:
        logger.error(f"人格配置异常: {str(e)}")
    return config

def step_identity(config):
    print("\n=== 配置身份特征 ===")
    try:
        details = []
        while True:
            detail = input(f"请输入第{len(details)+1}条身份特征（留空结束）：").strip()
            if not detail:
                break
            details.append(detail)
        if details:
            config['identity']['identity_detail'] = details
        for field in ['age']:
            while True:
                value = input(f"请输入{field}（当前：{config['identity'][field]}）：").strip()
                if not value:
                    break
                if value.isdigit():
                    config['identity'][field] = int(value)
                    break
                logger.warning("输入非法，已使用默认值。")
                break
        gender = input(f"请输入性别（男/女，当前：{config['identity']['gender']}）：").strip()
        if gender in ['男', '女']:
            config['identity']['gender'] = gender
        elif gender:
            logger.warning("输入非法，已使用默认值。")
        appearance = input(f"请输入外貌描述（当前：{config['identity']['appearance']}）：").strip()
        if appearance:
            config['identity']['appearance'] = appearance
    except Exception as e:
        logger.error(f"身份特征配置异常: {str(e)}")
    return config

def step_schedule(config):
    print("\n=== 配置日程表 ===")
    try:
        schedule = config.get('schedule', {})
        enable_gen = input(f"是否启用日程表？[是/否]（当前：{'是' if schedule.get('enable_schedule_gen', True) else '否'}）：").strip().lower()
        if enable_gen in ['y', 'yes', '是']:
            schedule['enable_schedule_gen'] = True
        elif enable_gen in ['n', 'no', '否']:
            schedule['enable_schedule_gen'] = False
        elif enable_gen:
            logger.warning("输入非法，已使用默认值。")
        enable_inter = input(f"日程表是否影响回复模式？[是/否]（当前：{'是' if schedule.get('enable_schedule_interaction', True) else '否'}）：").strip().lower()
        if enable_inter in ['y', 'yes', '是']:
            schedule['enable_schedule_interaction'] = True
        elif enable_inter in ['n', 'no', '否']:
            schedule['enable_schedule_interaction'] = False
        elif enable_inter:
            logger.warning("输入非法，已使用默认值。")
        prompt = input(f"日程表生成提示（当前：{schedule.get('prompt_schedule_gen', '')}）：").strip()
        if prompt:
            schedule['prompt_schedule_gen'] = prompt
        interval = input(f"日程表更新间隔（秒，当前：{schedule.get('schedule_doing_update_interval', 900)}）：").strip()
        if interval.isdigit():
            schedule['schedule_doing_update_interval'] = int(interval)
        elif interval:
            logger.warning("输入非法，已使用默认值。")
        config['schedule'] = schedule
    except Exception as e:
        logger.error(f"日程表配置异常: {str(e)}")
    return config

def step_response(config):
    print("\n=== 配置回复策略 ===")
    try:
        response = config.get('response', {})
        hfc = input(f"是否启用heart_flowC模式？[是/否]（当前：{'是' if response.get('enable_heart_flowC', True) else '否'}）：").strip().lower()
        if hfc in ['y', 'yes', '是']:
            response['enable_heart_flowC'] = True
        elif hfc in ['n', 'no', '否']:
            response['enable_heart_flowC'] = False
        elif hfc:
            logger.warning("输入非法，已使用默认值。")
        config['response'] = response
    except Exception as e:
        logger.error(f"回复策略配置异常: {str(e)}")
    return config

def step_message(config):
    print("\n=== 配置消息参数 ===")
    try:
        message = config.get('message', {})
        emoji_chance = input(f"表情包概率（0-1，当前：{message.get('emoji_chance', 0.2)}）：").strip()
        if emoji_chance:
            try:
                message['emoji_chance'] = float(emoji_chance)
            except ValueError:
                logger.warning("输入非法，已使用默认值。")
        timeout = input(f"最长思考时间（秒，当前：{message.get('thinking_timeout', 100)}）：").strip()
        if timeout.isdigit():
            message['thinking_timeout'] = int(timeout)
        elif timeout:
            logger.warning("输入非法，已使用默认值。")
        max_len = input(f"最大回复长度（token，当前：{message.get('max_response_length', 256)}）：").strip()
        if max_len.isdigit():
            message['max_response_length'] = int(max_len)
        elif max_len:
            logger.warning("输入非法，已使用默认值。")
        buffer = input(f"启用消息缓冲器？[是/否]（当前：{'是' if message.get('message_buffer', True) else '否'}）：").strip().lower()
        if buffer in ['y', 'yes', '是']:
            message['message_buffer'] = True
        elif buffer in ['n', 'no', '否']:
            message['message_buffer'] = False
        elif buffer:
            logger.warning("输入非法，已使用默认值。")
        config['message'] = message
    except Exception as e:
        logger.error(f"消息参数配置异常: {str(e)}")
    return config

def step_willing(config):
    print("\n=== 配置回复意愿 ===")
    try:
        willing = config.get('willing', {})
        mode = input(f"回复意愿模式（classical/dynamic/mxp/custom，当前：{willing.get('willing_mode', 'classical')}）：").strip()
        if mode:
            if mode in ['classical', 'dynamic', 'mxp', 'custom']:
                willing['willing_mode'] = mode
            else:
                logger.warning("输入非法，已使用默认值。")
        mentioned = input(f"提及bot必然回复？[是/否]（当前：{'是' if willing.get('mentioned_bot_inevitable_reply', False) else '否'}）：").strip().lower()
        if mentioned in ['y', 'yes', '是']:
            willing['mentioned_bot_inevitable_reply'] = True
        elif mentioned in ['n', 'no', '否']:
            willing['mentioned_bot_inevitable_reply'] = False
        elif mentioned:
            logger.warning("输入非法，已使用默认值。")
        at_bot = input(f"@bot必然回复？[是/否]（当前：{'是' if willing.get('at_bot_inevitable_reply', False) else '否'}）：").strip().lower()
        if at_bot in ['y', 'yes', '是']:
            willing['at_bot_inevitable_reply'] = True
        elif at_bot in ['n', 'no', '否']:
            willing['at_bot_inevitable_reply'] = False
        elif at_bot:
            logger.warning("输入非法，已使用默认值。")
        config['willing'] = willing
    except Exception as e:
        logger.error(f"回复意愿配置异常: {str(e)}")
    return config

def step_emoji(config):
    print("\n=== 配置表情包 ===")
    try:
        emoji = config.get('emoji', {})
        max_num = input(f"表情包最大数量（当前：{emoji.get('max_emoji_num', 40)}）：").strip()
        if max_num.isdigit():
            emoji['max_emoji_num'] = int(max_num)
        elif max_num:
            logger.warning("输入非法，已使用默认值。")
        max_del = input(f"达到最大数量时删除表情包？[是/否]（当前：{'是' if emoji.get('max_reach_deletion', True) else '否'}）：").strip().lower()
        if max_del in ['y', 'yes', '是']:
            emoji['max_reach_deletion'] = True
        elif max_del in ['n', 'no', '否']:
            emoji['max_reach_deletion'] = False
        elif max_del:
            logger.warning("输入非法，已使用默认值。")
        interval = input(f"检查表情包间隔（分钟，当前：{emoji.get('check_interval', 10)}）：").strip()
        if interval.isdigit():
            emoji['check_interval'] = int(interval)
        elif interval:
            logger.warning("输入非法，已使用默认值。")
        save_pic = input(f"保存图片？[是/否]（当前：{'是' if emoji.get('save_pic', False) else '否'}）：").strip().lower()
        if save_pic in ['y', 'yes', '是']:
            emoji['save_pic'] = True
        elif save_pic in ['n', 'no', '否']:
            emoji['save_pic'] = False
        elif save_pic:
            logger.warning("输入非法，已使用默认值。")
        save_emoji = input(f"保存表情包？[是/否]（当前：{'是' if emoji.get('save_emoji', False) else '否'}）：").strip().lower()
        if save_emoji in ['y', 'yes', '是']:
            emoji['save_emoji'] = True
        elif save_emoji in ['n', 'no', '否']:
            emoji['save_emoji'] = False
        elif save_emoji:
            logger.warning("输入非法，已使用默认值。")
        steal = input(f"偷取表情包？[是/否]（当前：{'是' if emoji.get('steal_emoji', True) else '否'}）：").strip().lower()
        if steal in ['y', 'yes', '是']:
            emoji['steal_emoji'] = True
        elif steal in ['n', 'no', '否']:
            emoji['steal_emoji'] = False
        elif steal:
            logger.warning("输入非法，已使用默认值。")
        enable_check = input(f"启用表情包过滤？[是/否]（当前：{'是' if emoji.get('enable_check', False) else '否'}）：").strip().lower()
        if enable_check in ['y', 'yes', '是']:
            emoji['enable_check'] = True
            check_prompt = input(f"表情包过滤要求（当前：{emoji.get('check_prompt', '')}）：").strip()
            if check_prompt:
                emoji['check_prompt'] = check_prompt
        elif enable_check in ['n', 'no', '否']:
            emoji['enable_check'] = False
        elif enable_check:
            logger.warning("输入非法，已使用默认值。")
        config['emoji'] = emoji
    except Exception as e:
        logger.error(f"表情包配置异常: {str(e)}")
    return config

def step_chinese_typo(config):
    print("\n=== 配置中文错别字生成器 ===")
    try:
        typo = config.get('chinese_typo', {})
        enable = input(f"启用中文错别字生成器？[是/否]（当前：{'是' if typo.get('enable', True) else '否'}）：").strip().lower()
        if enable in ['y', 'yes', '是']:
            typo['enable'] = True
        elif enable in ['n', 'no', '否']:
            typo['enable'] = False
        elif enable:
            logger.warning("输入非法，已使用默认值。")
        config['chinese_typo'] = typo
    except Exception as e:
        logger.error(f"中文错别字配置异常: {str(e)}")
    return config

def step_response_splitter(config):
    print("\n=== 配置回复分割器 ===")
    try:
        splitter = config.get('response_splitter', {})
        enable = input(f"启用回复分割器？[是/否]（当前：{'是' if splitter.get('enable_response_splitter', True) else '否'}）：").strip().lower()
        if enable in ['y', 'yes', '是']:
            splitter['enable_response_splitter'] = True
        elif enable in ['n', 'no', '否']:
            splitter['enable_response_splitter'] = False
        elif enable:
            logger.warning("输入非法，已使用默认值。")
        max_len = input(f"回复最大长度（当前：{splitter.get('response_max_length', 256)}）：").strip()
        if max_len.isdigit():
            splitter['response_max_length'] = int(max_len)
        elif max_len:
            logger.warning("输入非法，已使用默认值。")
        max_sent = input(f"回复最大句子数（当前：{splitter.get('response_max_sentence_num', 4)}）：").strip()
        if max_sent.isdigit():
            splitter['response_max_sentence_num'] = int(max_sent)
        elif max_sent:
            logger.warning("输入非法，已使用默认值。")
        kaomoji = input(f"启用颜文字保护？[是/否]（当前：{'是' if splitter.get('enable_kaomoji_protection', False) else '否'}）：").strip().lower()
        if kaomoji in ['y', 'yes', '是']:
            splitter['enable_kaomoji_protection'] = True
        elif kaomoji in ['n', 'no', '否']:
            splitter['enable_kaomoji_protection'] = False
        elif kaomoji:
            logger.warning("输入非法，已使用默认值。")
        config['response_splitter'] = splitter
    except Exception as e:
        logger.error(f"回复分割器配置异常: {str(e)}")
    return config

def step_experimental(config):
    print("\n=== 配置实验性功能 ===")
    print("解释：")
    print("1. enable_friend_chat(启用好友聊天)：允许机器人在私聊中回复")
    print("2. pfc_chatting(启用PFC聊天)：启用基于上下文的聊天")
    try:
        for field in ['enable_friend_chat', 'pfc_chatting']:
            current = "是" if config['experimental'][field] else "否"
            answer = input(f"是否启用 {field}？[是/否]（当前：{current}）：").strip().lower()
            if answer in ['y', 'yes', '是']:
                config['experimental'][field] = True
            elif answer in ['n', 'no', '否']:
                config['experimental'][field] = False
            elif answer:
                logger.warning("输入非法，已使用默认值。")
    except Exception as e:
        logger.error(f"实验性功能配置异常: {str(e)}")
    return config

def step_info_extraction():
    print("\n=== 配置信息抽取线程数 ===")
    try:
        import toml
        lpmm_data = {}
        if os.path.exists(LPMM_CONFIG_PATH):
            with open(LPMM_CONFIG_PATH, "r", encoding="utf-8") as f:
                lpmm_data = toml.load(f)
        info_extraction = lpmm_data.get("info_extraction", {})
        current_workers = info_extraction.get("workers", 10)
        value = input(f"实体提取最大线程数（当前：{current_workers}）：").strip()
        if value:
            if value.isdigit() and int(value) > 0:
                info_extraction["workers"] = int(value)
            else:
                logger.warning("输入非法，已使用默认值。")
                info_extraction["workers"] = 10
        else:
            info_extraction["workers"] = current_workers
        lpmm_data["info_extraction"] = info_extraction
        with open(LPMM_CONFIG_PATH, "w", encoding="utf-8") as f:
            toml.dump(lpmm_data, f)
        logger.success(f"已保存info_extraction配置，workers={info_extraction['workers']}")
    except Exception as e:
        logger.error(f"写入lpmm_config.toml info_extraction配置失败: {str(e)}")

def step_api_keys(config):
    print("\n=== 配置API密钥 ===")
    try:
        env_path = os.path.join(BASE_DIR, ".env")
        current_env = dotenv_values(env_path)
        if os.path.exists(env_path):
            current_env = dotenv_values(env_path)
        current_key = current_env.get("SILICONFLOW_KEY", "")
        print("请前往 https://cloud.siliconflow.cn/account/ak 获取API秘钥")
        new_key = input(f"请输入SILICONFLOW_API密钥（当前：{current_key}，留空保持当前）：").strip()
        if new_key:
            os.environ["SILICONFLOW_KEY"] = new_key
            env_lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    env_lines = f.readlines()
            found = False
            for i, line in enumerate(env_lines):
                if line.startswith("SILICONFLOW_KEY="):
                    env_lines[i] = f"SILICONFLOW_KEY={new_key}\n"
                    found = True
                    break
            if not found:
                env_lines.append(f"\nSILICONFLOW_KEY={new_key}")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(env_lines)
            # 同步写入lpmm_config.toml
            try:
                import toml
                if os.path.exists(LPMM_CONFIG_PATH):
                    with open(LPMM_CONFIG_PATH, "r", encoding="utf-8") as f:
                        lpmm_data = toml.load(f)
                else:
                    lpmm_data = {}
                providers = lpmm_data.get("llm_providers", [])
                updated = False
                for provider in providers:
                    if provider.get("name") == "siliconflow":
                        provider["api_key"] = new_key
                        updated = True
                        break
                if not updated:
                    # 若没有则添加
                    providers.append({
                        "name": "siliconflow",
                        "base_url": "https://api.siliconflow.cn/v1/",
                        "api_key": new_key
                    })
                lpmm_data["llm_providers"] = providers
                with open(LPMM_CONFIG_PATH, "w", encoding="utf-8") as f:
                    toml.dump(lpmm_data, f)
                logger.success("已同步API密钥到lpmm_config.toml")
            except Exception as e:
                logger.error(f"同步API密钥到lpmm_config.toml失败: {str(e)}")
    except Exception as e:
        logger.error(f"API密钥配置异常: {str(e)}")
    return config

def main():
    print("---欢迎使用简易配置向导！---")
    print("请按照提示输入配置信息。")
    print("留空则使用默认值，直接回车跳过该步骤。")
    print("输入 Ctrl+C 取消配置。")
    print("------------------------")
    print("注意！！此配置向导只是简易版")
    print("想要配置更多请使用根目录最下面的可视化配置文件修改器！")
    print("------------------------")
    print("制作 By MotricSeven")
    print("------------------------\n")

    try:
        backup_config()
        config = load_config()
        steps = [
            step_bot,
            step_groups,
            step_personality,
            step_identity,
            step_schedule,
            step_response,
            step_message,
            step_willing,
            step_emoji,
            step_chinese_typo,
            step_response_splitter,
            step_experimental,
            step_info_extraction,  # 新增
            step_api_keys
        ]
        for step in steps:
            # step_info_extraction 不需要传参
            if step == step_info_extraction:
                step()
            else:
                config = step(config)
        save_config(config)
        print("\n配置已保存！")
    except Exception as e:
        logger.critical(f"配置流程异常终止: {str(e)}")

if __name__ == "__main__":
    main()