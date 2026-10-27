from src.common.server import global_server
import os
from maim_message import MessageServer


global_api = MessageServer(host=os.environ["HOST"], port=int(os.environ["PORT"]), app=global_server.get_app())
