# -*- coding: utf-8 -*-
"""兼容转发层 —— 模块已迁到 :mod:`wwdm_app.core.logging`（v5.0 拆架构）。

保留 1~2 个版本：``from . import logging_setup; logging_setup.setup()`` 仍可用，
新代码请直接 ``from .core import logging as wwdm_logging``。
"""
from .core.logging import (         # noqa: F401
    LOG_DIR,
    LOG_FILE,
    get_logger,
    setup,
)
