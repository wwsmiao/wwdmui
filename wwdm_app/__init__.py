from flask import Flask

app = Flask(__name__)

from . import config    # 加载设置 (settings)
from . import services  # aria2c/git clone 服务
from . import database  # 数据库操作
from . import routes    # 注册所有路由
routes.register(app)
