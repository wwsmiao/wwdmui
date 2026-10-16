# -*- coding: utf-8 -*-
"""兼容转发层 —— 模块已迁到 :mod:`wwdm_app.core.errors`（v5.0 拆架构）。

保留 1~2 个版本：老代码里 ``from .errors import ...`` 仍然可用，
新代码请直接 ``from .core.errors import ...``（或 ``from ..core.errors import ...``）。
"""
from .core.errors import (          # noqa: F401
    TraceIdFilter,
    error_payload,
    get_trace_id,
    new_trace_id,
    register,
    set_trace_id,
    wants_json,
)
