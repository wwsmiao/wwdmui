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

    from .core import config                  # 加载设置 (settings)
    from .core import errors                  # 请求追踪号 trace_id + 统一错误体
    from .core import logging as wwdm_logging
    from .core import security                # 访问令牌 / 同源校验 / 上传上限
    from .core import tasks                   # 任务总线（后台活儿统一上报）

    wwdm_logging.setup()   # 日志：控制台 + logs/wwdm.log（Werkzeug 噪音也在里面降级）

    from . import services                    # aria2c / git clone 服务
    from .core import db                      # 数据库操作
    from . import routes                      # 注册所有路由

    routes.register(app)


    from .blueprints import notes as bp_notes      # v5.0 第 6 步：域 2/10
    app.register_blueprint(bp_notes.bp)

    from .blueprints import shutdown as bp_shutdown  # v5.0 第 6 步：域 3/10
    app.register_blueprint(bp_shutdown.bp)

    from .blueprints import plugins as bp_plugins    # v5.0 第 6 步：域 4/10
    app.register_blueprint(bp_plugins.bp)

    from .blueprints import clone as bp_clone        # v5.0 第 6 步：域 5/10
    app.register_blueprint(bp_clone.bp)

    from .blueprints import files as bp_files        # v5.0 第 6 步：域 6/11
    app.register_blueprint(bp_files.bp)

    from .blueprints import workflows as bp_workflows  # v5.0 第 6 步：域 7/11
    app.register_blueprint(bp_workflows.bp)

    from .blueprints import output as bp_output      # v5.0 第 6 步：域 8/11
    app.register_blueprint(bp_output.bp)

    from .blueprints import input as bp_input        # v5.0 第 6 步：域 9/11
    app.register_blueprint(bp_input.bp)

    from .blueprints import models as bp_models      # v5.0 第 6 步：域 10/11
    app.register_blueprint(bp_models.bp)

    from .blueprints import download as bp_download  # v5.0 第 6 步：域 11/11
    app.register_blueprint(bp_download.bp)

    from .blueprints import settings as bp_settings  # 第 6 步收尾：设置 / 系统
    app.register_blueprint(bp_settings.bp)

    from .blueprints import tools as bp_tools        # 第 6 步收尾：工具检测 + GitHub 镜像
    app.register_blueprint(bp_tools.bp)

    from .blueprints import comfyui as bp_comfyui    # 第 6 步收尾：ComfyUI 启动器
    app.register_blueprint(bp_comfyui.bp)

    from .blueprints import repair as bp_repair      # 第 6 步收尾：修复安装
    app.register_blueprint(bp_repair.bp)

    from .blueprints import audit as bp_audit        # v5.1：操作审计日志（S7，只读）
    app.register_blueprint(bp_audit.bp)





    errors.register(app)    # 必须早于 security：先分配 trace_id，security 的日志才带号
    security.register(app)
    tasks.register(app)     # 只读的 GET /api/tasks；不参与鉴权闸，放最后
    return app


# 兼容层：老代码 `from wwdm_app import app` 仍可用（保留 1~2 个版本）
app = create_app()
