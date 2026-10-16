# -*- coding: utf-8 -*-
"""日志：控制台 + 滚动文件，并给每条记录挂上请求追踪号 trace_id。

设计
----
* 只配置一个顶层 logger ``wwdm``；子模块用 ``wwdm.services`` / ``wwdm.errors`` 这类
  名字命名即可自动继承（``propagate`` 不关，handler 只挂在顶层）。
* 文件落在 ``BASE_DIR/logs/wwdm.log``，``RotatingFileHandler`` 5 MB × 3 份。
  打不开就只降级到控制台，**绝不因为日志把应用搞挂**。
* 控制台输出与原来的 ``print`` 保持一致 —— 用户习惯在启动窗口看进度。
  唯一例外是「每秒刷新一行的下载进度条」，那种高频输出走 ``services._cli()``，
  只打控制台、不进日志文件。
* trace_id 由 :mod:`wwdm_app.errors` 在每个请求开始时写进线程局部变量，
  这里用一个 Filter 塞进 LogRecord。非请求线程（克隆/下载后台线程）显示为 ``-``。
"""
import logging
import logging.handlers
import os

from . import config

LOG_DIR = os.path.join(config.BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "wwdm.log")

_FMT = "%(asctime)s %(levelname)-7s [%(trace_id)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False
_file_ok = False


def _filter():
    # 延迟导入：logging_setup 属于底层，不该在导入期牵扯 request/Flask
    from .errors import TraceIdFilter
    return TraceIdFilter()


def _has_file_handler(logger):
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            return True
    return False


def setup(level=logging.INFO):
    """幂等地配置日志。返回 True 表示文件日志可用。"""
    global _configured, _file_ok
    root = logging.getLogger("wwdm")

    if _configured:
        return _has_file_handler(root)

    root.setLevel(level)
    fmt = logging.Formatter(_FMT, _DATEFMT)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(_filter())
    root.addHandler(sh)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(_filter())
        root.addHandler(fh)
        _file_ok = True
    except Exception as e:                      # 目录只读 / 磁盘满 / 权限不足
        _file_ok = False
        root.warning("日志文件不可用，本次仅输出到控制台：%s", e)

    # Werkzeug 的逐请求日志噪音太大，只留 WARNING 以上（与 __init__.py 原设置一致）
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True
    if _file_ok:
        root.info("日志已就绪 -> %s", LOG_FILE)
    return _file_ok


def get_logger(name):
    """取一个 ``wwdm.*`` 子 logger（方便其他模块统一入口）"""
    return logging.getLogger(name if name.startswith("wwdm") else "wwdm." + name)
