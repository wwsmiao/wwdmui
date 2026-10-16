# -*- coding: utf-8 -*-
"""兼容转发层 —— 实现已迁到 :mod:`wwdm_app.core.config`（v5.0 拆架构）。

为什么用「替换 sys.modules」而不是 ``from .core.config import *``
------------------------------------------------------------------
* 星号导入要把所有公开名逐个列出。将来 ``core/config.py`` 新增一个函数而忘了
  在这里同步，调用方就会 **AttributeError**，而且只在真跑到那一行时才炸。
* 替换之后，``wwdm_app.config`` 与 ``wwdm_app.core.config`` 是**同一个模块对象**：
  属性读写、``is`` 判等、``config.save_settings = ...`` 这类打桩，
  全与「没搬过」完全一致。
* 也正因为是同一个对象，``from .config import settings`` 绑到的仍然是那个 dict，
  所以 ``api_reset_settings`` 那套**原地改**（``clear()`` + ``update()``）照旧成立。

新代码请直接 ``from .core import config``。
"""
import sys

from .core import config as _impl

sys.modules[__name__] = _impl
