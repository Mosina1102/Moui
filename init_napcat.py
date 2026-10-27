import re
import json
import tomli
from pathlib import Path

def is_valid_qq(qq_str):
    # 检查是否为纯数字
    return bool(re.match(r'^\d+$', qq_str))

def create_napcat_config(qq_number):
    # 创建napcat配置文件
    config = {
        "fileLog": False,
        "consoleLog": True,
        "fileLogLevel": "debug",
        "consoleLogLevel": "info",
        "packetBackend": "auto",
        "packetServer": "",
        "o3HookMode": 1
    }
    
    # 确保目录存在
    config_dir = Path('./napcat/versions/9.9.19-34362/resources/app/napcat/config')
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建配置文件
    config_path = config_dir / f'napcat_{qq_number}.json'
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def create_onebot_config(qq_number):
    # 创建OneBot11配置文件
    config = {
    "network": {
        "httpServers": [],
        "httpSseServers": [],
        "httpClients": [],
        "websocketServers": [],
        "websocketClients": [
            {
                "enable": True,
                "name": "MaiBot Main",
                "url": "ws://localhost:8095",
                "reportSelfMessage": False,
                "messagePostFormat": "array",
                "token": "",
                "debug": False,
                "heartInterval": 30000,
                "reconnectInterval": 30000
            }
        ],
        "plugins": []
    },
    "musicSignUrl": "",
    "enableLocalFile2Url": False,
    "parseMultMsg": False
    }
    
    # 确保目录存在
    config_dir = Path('./napcat/versions/9.9.19-34362/resources/app/napcat/config')
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建配置文件
    config_path = config_dir / f'onebot11_{qq_number}.json'
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def update_qq_in_config(path: str ,qq_number: int):
    config_path = Path(path)
    
    # 读取原始文件内容，包括注释
    with open(config_path, 'rb') as f:
        config = tomli.load(f)
    
    # 只更新qq值
    config['bot']['qq'] = int(qq_number)
    
    # 读取原始文件内容以保留注释
    with open(config_path, 'r', encoding='utf-8') as f:
        original_content = f.read()
    
    # 使用正则表达式替换qq值，保留其他内容和注释
    updated_content = re.sub(
        r'(\[bot\][^\[]*qq\s*=\s*)\d+',
        rf'\g<1>{qq_number}',
        original_content
    )
    
    # 写入更新后的内容
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)

def main():
    while True:
        qq_number = input('请输入QQ号：')
        if not is_valid_qq(qq_number):
            print('错误：请输入有效的QQ号（纯数字）')
            continue
        
        try:
            update_qq_in_config('./config/bot_config.toml',qq_number)
            update_qq_in_config('./template/bot_config_template.toml',qq_number)
            create_onebot_config(qq_number)
            create_napcat_config(qq_number)
            print(f'成功更新QQ号为：{qq_number}并创建所有必要的配置文件')
            break
        except Exception as e:
            print(f'更新配置文件时出错：{str(e)}')
            continue

if __name__ == '__main__':
    main()