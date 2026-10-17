# -*- coding: utf-8 -*-
"""下载 / aria2c API 蓝图（v5.0 第 6 步 域 11/11）。

⚠️ 本域是唯一「只拆路由、不拆 services」的域：
   aria2_proc 被 services/__init__.py 里 aria2_start / aria2_stop /
   kill_aria2_processes / cleanup_all 四处 `global` 重绑定，函数一搬走，
   包上的 services.aria2_proc 立刻变陈旧副本（启动/停止状态读不到）。
   改访问器属行为变更，风险大于收益 —— 所以 services 侧原样不动，
   这里只把 11 条路由搬出来，让 routes.py 变薄。

svc.queue_lock / svc.download_queue / svc.download_mirrors 都是跨模块共享的
活对象，按模块属性直取（不得在别处重新赋值）。
"""
import os
import subprocess
import platform as _platform

from flask import Blueprint, jsonify, request

from .. import config
from .. import services as svc
from ..routes import _tool_version_cached

bp = Blueprint("download", __name__)


# ========== aria2c API ==========
@bp.route("/api/aria2/start", methods=["POST"])
def api_a2_start():
    ok, msg = svc.aria2_start()
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/aria2/stop", methods=["POST"])
def api_a2_stop():
    return jsonify({"ok": True, "msg": svc.aria2_stop()})

@bp.route("/api/aria2/status", methods=["GET"])
def api_a2_status():
    r, e = svc.aria2_rpc("aria2.getVersion")
    return jsonify({"running": True, "version": r.get("version", "?")}) if not e else jsonify({"running": False})

@bp.route("/api/aria2/detect", methods=["GET"])
def api_a2_detect():
    path = svc.get_aria2c_path()
    found = os.path.isfile(path)
    if not found and config.IS_LINUX:
        try:
            rr = subprocess.run(["which", "aria2c"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
            if rr.returncode == 0:
                path = rr.stdout.strip()
                found = True
        except Exception:
            pass
    ver = _tool_version_cached("aria2:" + str(path), [path, "--version"]) if found else None
    return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

# ========== 下载 API ==========
@bp.route("/api/download/start/<int:rid>", methods=["POST"])
def api_dl_start(rid):
    d = request.get_json(silent=True) or {}
    ok, msg = svc.start_download(rid, use_mirror=bool(d.get("mirror")))
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/download/batch", methods=["POST"])
def api_dl_batch():
    d = request.get_json(silent=True) or {}
    rids = d.get("ids", [])
    if not isinstance(rids, list) or not rids:
        return jsonify({"ok": False, "msg": "ids无效"}), 400
    return jsonify({"ok": True, "results": svc.start_batch(rids, bool(d.get("mirror")))})

@bp.route("/api/download/pause/<int:rid>", methods=["POST"])
def api_dl_pause(rid):
    ok, msg = svc.pause_download(rid)
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/download/resume/<int:rid>", methods=["POST"])
def api_dl_resume(rid):
    ok, msg = svc.resume_download(rid)
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/download/cancel/<int:rid>", methods=["POST"])
def api_dl_cancel(rid):
    ok, msg = svc.cancel_download(rid)
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/download/cancel_queue/<int:rid>", methods=["POST"])
def api_dl_cancel_queue(rid):
    with svc.queue_lock:
        if rid in svc.download_queue:
            svc.download_queue.remove(rid)
            svc.download_mirrors.pop(rid, None)
            return jsonify({"ok": True, "msg": "已从队列移除"})
    return jsonify({"ok": False, "msg": "不在队列中"})

@bp.route("/api/download/status/<int:rid>", methods=["GET"])
def api_dl_status(rid):
    st = svc.get_download_status(rid)
    return jsonify({"status": "none"}) if st is None else jsonify(st)

@bp.route("/api/download/all_status", methods=["GET"])
def api_dl_all_status():
    return jsonify(svc.get_all_download_status())
