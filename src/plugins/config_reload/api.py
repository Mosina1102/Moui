from fastapi import APIRouter, HTTPException

# 创建APIRouter而不是FastAPI实例
router = APIRouter()


@router.post("/reload-config")
async def reload_config():
    try:  # TODO: 实现配置重载
        # bot_config_path = os.path.join(BotConfig.get_config_dir(), "bot_config.toml")
        # BotConfig.reload_config(config_path=bot_config_path)
        return {"message": "TODO: 实现配置重载", "status": "unimplemented"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重载配置时发生错误: {str(e)}") from e
