import logging
from flask import Flask

# 关闭 Flask/Werkzeug 的 HTTP 请求日志，只保留 WARNING 及以上
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)

from . import config    # 加载设置 (settings)
from . import services  # aria2c/git clone 服务
from . import database  # 数据库操作
from . import routes    # 注册所有路由
routes.register(app)
