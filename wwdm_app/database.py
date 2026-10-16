# -*- coding: utf-8 -*-
"""兼容转发层 —— 实现已迁到 :mod:`wwdm_app.core.db`（v5.0 拆架构）。

本模块有 38 个顶层函数，逐个 re-export 既啰嗦又容易漏，所以直接做**模块对象
替换**：``wwdm_app.database`` 与 ``wwdm_app.core.db`` 是同一个对象，
``from .database import db_get, ...``、``from . import database as _db``、
以及测试里 ``db.DB_PATH = 副本`` 全部照旧生效。

新代码请直接 ``from .core import db``。
"""
import sys

from .core import db as _impl

sys.modules[__name__] = _impl
