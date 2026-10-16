from flask import Flask

app = Flask(__name__)

from . import config          # 加载设置 (settings)
from . import errors          # 请求追踪号 trace_id + 统一错误体
from . import logging_setup
logging_setup.setup()         # 日志：控制台 + logs/wwdm.log（Werkzeug 噪音也在里面降级）
from . import services        # aria2c / git clone 服务
from . import database        # 数据库操作
from . import routes          # 注册所有路由
from . import security        # 访问令牌 / 同源校验 / 上传上限

routes.register(app)
errors.register(app)          # 必须早于 security：先分配 trace_id，security 的日志才带号
security.register(app)
