# -*- coding: utf-8 -*-
"""定时关机路由（v5.0 第 6 步 · 域 3/10）。

从 ``routes.py`` 的 ``register(app)`` 尾部整段搬来，只做 ``@app.route`` → ``@bp.route``
与去一级缩进，路由规则 / 方法 / 返回体逐字未变。

⚠️ 这是**计划里 system 域的一个切片**：``/api/settings``、``/api/paths``、
``/api/system/quit``、``/api/wallpaper``、``/api/github/*``、``/api/{git,python,aria2}/detect``
仍留在 ``routes.py``，等后续提交再并入 ``blueprints/system.py``。
先切关机是因为它在 services 侧是一段**完全连续、零跨域依赖**的块，风险最低。
"""
from flask import Blueprint, jsonify, request

from .. import config
from .. import services as svc

bp = Blueprint("shutdown", __name__)


@bp.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    d = request.get_json(silent=True) or {}
    # 延迟的取值与校验统一放在 services.schedule_shutdown 里，
    # 避免这里再兜一层 int() 把非法值悄悄改成 60（会无视用户配置）。
    delay = d.get("delay", config.settings.get("shutdown_delay", 60))
    return jsonify(svc.schedule_shutdown(delay))

@bp.route("/api/shutdown/cancel", methods=["POST"])
def api_shutdown_cancel():
    return jsonify(svc.cancel_shutdown())

@bp.route("/api/shutdown/status")
def api_shutdown_status():
    return jsonify(svc.shutdown_status())
