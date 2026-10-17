# -*- coding: utf-8 -*-
"""操作审计日志读取 API（S7，v5.1 · 蓝域 16/16）。

只读：审计记录由各蓝线在自己的危险动作处埋点写（``svc.audit(...)``），
这里不提供写入口 —— 避免「随便哪个请求都能伪造/清空审计」。

表初始化沿用 notes 蓝图的 ``@bp.record_once`` 套路：每个 app 注册时跑一次。
"""
from flask import Blueprint, jsonify, request

from .. import services as svc
from ..core import db as coredb

bp = Blueprint("audit", __name__)


@bp.record_once
def _audit_init(state):
    """等价于 notes 蓝图的 _notes_init：建 audit_log 表（老库自动升级）。

    用 record_once 而不是模块级直接调，是为了保住「create_app() 调几次就
    初始化几次」的原语义（模块级只会随 import 跑一次）。
    """
    coredb.audit_log_init()


@bp.route("/api/audit")
def api_audit():
    """最近的操作审计（id 倒序）。

    query:
        limit  默认 100，上限 500
        action 按动作 key 过滤（见 AUDIT_ACTIONS）
        kw     对象 / 详情关键词模糊匹配
    """
    action = (request.args.get("action") or "").strip()
    kw = (request.args.get("kw") or "").strip()
    try:
        limit = int(request.args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"ok": True,
                    "actions": svc.AUDIT_ACTIONS,
                    "logs": svc.audit_list(limit=limit, action=action, kw=kw)})
