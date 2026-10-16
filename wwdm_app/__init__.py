# -*- coding: utf-8 -*-
"""ComfyUI 模型管理器 —— 应用工厂。

v5.0 拆架构第 2~4 步：把原先散落在**导入期**的初始化收进 :func:`create_app`。
好处是测试可以拿到互相隔离的 app 实例，不必再依赖「import wwdm_app 就顺手把
设置、日志、数据库、路由全部建好」这种隐式副作用。

模块末尾仍保留 ``app = create_app()`` 作为兼容层，老代码
``from wwdm_app import app`` 继续可用（至少保留 1~2 个版本）。
"""
from flask import Flask


def create_app():
    """建立并配置一个 Flask 应用；每次调用返回**独立**实例。"""
    app = Flask(__name__)

    from . import config                      # 加载设置 (settings)
    from .core import errors                  # 请求追踪号 trace_id + 统一错误体
    from .core import logging as wwdm_logging
    from .core import security                # 访问令牌 / 同源校验 / 上传上限

    wwdm_logging.setup()   # 日志：控制台 + logs/wwdm.log（Werkzeug 噪音也在里面降级）

    from . import services                    # aria2c / git clone 服务
    from . import database                    # 数据库操作
    from . import routes                      # 注册所有路由

    routes.register(app)
    errors.register(app)    # 必须早于 security：先分配 trace_id，security 的日志才带号
    security.register(app)
    return app


# 兼容层：老代码 `from wwdm_app import app` 仍可用（保留 1~2 个版本）
app = create_app()
