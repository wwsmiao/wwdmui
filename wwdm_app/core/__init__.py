# -*- coding: utf-8 -*-
"""框架底座（v5.0 拆架构）。

这里只放「与具体业务无关、整个应用共享」的东西：

    config.py     路径常量 + DEFAULT_SETTINGS + settings 读写（save_settings）
    db.py         models / plugins / node_map / notes 四张表的全部数据访问
    logging.py    日志初始化（控制台 + logs/wwdm.log + trace_id 过滤器）
    errors.py     trace_id 分配 + 统一错误体 + 全局错误处理器
    security.py   访问令牌 / 同源校验 / 上传上限

⚠️ ``config.py`` 是靠 ``__file__`` 反推 PACKAGE_DIR 的：它在 core/ 下，所以
必须 `dirname` 两次才是 wwdm_app。**任何新搬进 core/ 的模块，只要用 __file__
算路径，都要先想清楚这一层。**

约定
----
* 本包内的模块**不要**再 `from . import xxx` 引用业务模块（services / routes），
  只能是底座依赖业务方向的反向依赖（业务模块可以 `from ..core import ...`）。
* 从业务目录引用本包统一写 ``from ..core.errors import ...`` 这种绝对形态，
  不要写 ``from .core import ...``（后者在包重命名时会静默失效）。
* 迁到这里的模块，在原来的位置**保留转发层**（见 ``wwdm_app/errors.py`` 等），
  至少保留 1~2 个版本，方便随时回退。
"""
