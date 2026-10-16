# -*- coding: utf-8 -*-
"""兼容转发层 —— 模块已迁到 :mod:`wwdm_app.core.security`（v5.0 拆架构）。

保留 1~2 个版本：``from . import security`` / ``from .security import ...`` 仍可用，
新代码请直接 ``from .core import security``。
"""
from .core.security import (        # noqa: F401
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    is_loopback,
    register,
    token_enabled,
)
