# -*- coding: utf-8 -*-
"""请求追踪号（trace_id）与统一错误体。

为什么需要
----------
1. 之前所有错误要么是 Flask 默认的 HTML 错误页、要么是散落各处手写的
   ``{"ok": False, "msg": ...}``，前端拿到 HTML 却按 JSON 解析就炸了。
2. 用户报「刚才点了下就报错了」时，没有任何可对上的标识 —— 现在每个请求
   都有 trace_id，它同时出现在日志行、响应头与错误响应体里。

约定
----
* trace_id 优先复用上游传进来的 ``X-Trace-Id``（便于跨服务串联），否则新生成 12 位。
* ``/api/*`` 一律返回 ``{"ok": false, "msg": ..., "trace_id": ...}``；页面请求返回极简 HTML。
* 4xx 记 WARNING、5xx 记 ERROR（带 traceback），全部落在 ``logs/wwdm.log``。
"""
import html
import logging
import threading
import uuid

from flask import current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

_LOG = logging.getLogger("wwdm.errors")

# 当前线程的 trace_id。用 threading.local 而不是 flask.g：
# 后台线程（克隆/下载）里没有请求上下文，但同样需要打日志。
_local = threading.local()

_HTTP_MSG = {
    400: "请求参数不正确",
    401: "未授权，需要访问令牌",
    403: "没有权限执行该操作",
    404: "接口或页面不存在",
    405: "请求方法不被允许",
    406: "无法按请求的格式返回内容",
    408: "请求超时",
    409: "状态冲突，请刷新后重试",
    412: "请求前置条件不满足",
    413: "上传内容超过上限",
    415: "不支持的请求类型",
    429: "请求过于频繁，请稍后再试",
    500: "服务器内部错误",
    502: "上游服务不可用",
    503: "服务暂时不可用",
}

_PAGE_TPL = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>出错了 · ComfyUI 模型管理器</title></head>
<body style="margin:0;padding:64px 24px;text-align:center;
font:14px/1.9 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#3a3a3a">
<h2 style="margin:0 0 8px;font-size:16px;font-weight:600">%s</h2>
<p style="margin:0;color:#9ca3af;font-size:12px">追踪号 %s</p>
</body></html>"""


# ============================================================
# trace_id
# ============================================================
def set_trace_id(tid):
    _local.trace_id = tid


def get_trace_id():
    return getattr(_local, "trace_id", "") or "-"


def new_trace_id():
    return uuid.uuid4().hex[:12]


class TraceIdFilter(logging.Filter):
    """把当前线程的 trace_id 塞进 LogRecord，日志格式里就能直接用 %(trace_id)s"""

    def filter(self, record):
        if not getattr(record, "trace_id", None):
            record.trace_id = get_trace_id()
        return True


# ============================================================
# 请求形态判定（security 也用它决定返回 JSON 还是 HTML）
# ============================================================
def wants_json():
    """这个请求期望 JSON 响应吗"""
    if (request.path or "").startswith("/api/"):
        return True
    if request.is_json:
        return True
    accept = request.headers.get("Accept", "")
    return ("application/json" in accept) and ("text/html" not in accept)


def error_payload(msg, extra=None):
    """构造统一错误体（给 security 等直接返回响应的模块复用）"""
    body = {"ok": False, "msg": msg, "trace_id": get_trace_id()}
    if extra:
        body.update(extra)
    return body


def _page(msg, tid):
    return _PAGE_TPL % (html.escape(str(msg)), html.escape(tid))


# ============================================================
# 注册
# ============================================================
def register(app):
    @app.before_request
    def _assign_trace():
        tid = (request.headers.get("X-Trace-Id") or "").strip()[:64] or new_trace_id()
        set_trace_id(tid)
        g.trace_id = tid

    @app.after_request
    def _expose_trace(resp):
        tid = getattr(g, "trace_id", None)
        if tid and "X-Trace-Id" not in resp.headers:
            try:
                resp.headers["X-Trace-Id"] = tid
            except Exception:
                pass
        return resp

    def _fail(code, msg, already_logged=False):
        tid = get_trace_id()
        if not already_logged:
            where = "%s %s" % (request.method, request.path)
            if request.query_string:
                where += "?" + request.query_string.decode("utf-8", "replace")
            if code >= 500:
                _LOG.error("[%s] %s -> %s %s", tid, where, code, msg)
            else:
                _LOG.warning("[%s] %s -> %s %s", tid, where, code, msg)
        if wants_json():
            return jsonify(error_payload(msg)), code
        return _page(msg, tid), code

    @app.errorhandler(HTTPException)
    def _http_error(e):
        code = e.code or 500
        if code == 413:
            try:
                gb = current_app.config.get("MAX_CONTENT_LENGTH", 0) / 1024.0 / 1024.0 / 1024.0
            except Exception:
                gb = 0.0
            msg = ("上传内容超过上限（当前上限 %.1f GB）。可在「设置 → 管理器访问」里"
                   "调整上限，或分批上传。" % gb)
        else:
            msg = _HTTP_MSG.get(code) or (getattr(e, "description", "") or ("错误 %d" % code))
        return _fail(code, msg)

    @app.errorhandler(Exception)
    def _unhandled(e):
        # 这里一定在异常处理栈里，exc_info 能拿到完整 traceback
        _LOG.error("未捕获异常: %s: %s", type(e).__name__, e, exc_info=True)
        msg = "服务器内部错误，请把追踪号提供给开发者"
        if current_app.debug:
            msg += "：" + str(e)
        return _fail(500, msg, already_logged=True)
