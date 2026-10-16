# -*- coding: utf-8 -*-
"""Flask 蓝图（v5.0 第 6 步起逐步从 routes.py 拆出）。

约定
----
* 一个域一个文件，文件名与 ``wwdm_app/services/<域>.py`` 同名。
* 只放路由；业务逻辑留在 services 侧。
* 用 ``bp = Blueprint("<域>", __name__)``，在 ``create_app()`` 里
  ``app.register_blueprint(...)`` 注册。
"""
