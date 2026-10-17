# -*- coding: utf-8 -*-
"""克隆 API 蓝图（v5.0 第 6 步 域 5/10）。

只放路由，不含业务逻辑；业务实现见 services/clone.py。
⚠️ clone_lock / clone_queue 是 services.clone 里的活对象，必须走模块属性直取
   （clone_svc.X），不能经包再导出的别名，否则将来重绑定后会指向旧对象。
"""
from flask import Blueprint, jsonify, request

from .. import services as svc
from ..services import clone as clone_svc

bp = Blueprint("clone", __name__)


# ========== 插件克隆 API ==========
@bp.route("/api/plugins/clone/<int:pid>", methods=["POST"])
def api_clone(pid):
    # 镜像一律取全局设置，页面不再传 mirror
    ok, msg = svc.start_clone(pid)
    if ok:
        p = svc.plugin_get(pid) or {}
        svc.audit("clone.start", target=p.get("name") or ("#%d" % pid),
                  detail=p.get("url") or "")
    else:
        svc.audit("clone.start", target="#%d" % pid, detail=msg, ok=False)
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/plugins/clone_batch", methods=["POST"])
def api_clone_batch():
    d = request.get_json(silent=True) or {}
    pids = d.get("ids", [])
    if not isinstance(pids, list) or not pids:
        return jsonify({"ok": False, "msg": "ids无效"}), 400
    results = svc.start_clone_batch(pids)
    started = [r for r in (results or []) if r.get("ok")]
    svc.audit("clone.start", target="批量 %d 个" % len(pids),
              detail="已发起 %d 个" % len(started))
    return jsonify({"ok": True, "results": results})
@bp.route("/api/plugins/clone_url", methods=["POST"])
def api_clone_by_url():
    """按仓库地址克隆插件（工作流解析弹窗的「下载」按钮）。

    解析给出的仓库来自上游映射表，个别不在插件库列表里 ——
    服务层会先补录条目拿到 id，再走既有的克隆流程。
    """
    d = request.get_json(silent=True) or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "缺少仓库地址"}), 400
    ok, msg, pid = svc.plugins_clone_by_url(url, d.get("name") or "")
    svc.audit("clone.start", target=url, detail=msg, ok=bool(ok))
    return jsonify({"ok": ok, "msg": msg, "id": pid})

@bp.route("/api/plugins/clone_status/<int:pid>", methods=["GET"])
def api_clone_status(pid):
    return jsonify(svc.get_clone_status(pid))

@bp.route("/api/plugins/clone_all_status", methods=["GET"])
def api_clone_all_status():
    return jsonify(svc.get_all_clone_status())

@bp.route("/api/plugins/clone_cancel/<int:pid>", methods=["POST"])
def api_clone_cancel(pid):
    ok, msg = svc.cancel_clone(pid)
    return jsonify({"ok": ok, "msg": msg})

@bp.route("/api/plugins/clone_cancel_cleanup", methods=["POST"])
def api_clone_cancel_cleanup():
    """停止单个克隆，并删掉它「克隆到一半」的文件夹（工作流解析弹窗的「停止」）。

    半成品目录留着会让下次克隆残缺或失败，所以这里顺手清掉；
    已克隆完成的插件目录不会被动（口径同「停止克隆」）。
    """
    d = request.get_json(silent=True) or {}
    try:
        pid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        pid = 0
    if not pid:
        return jsonify({"ok": False, "msg": "缺少插件 id"}), 400
    return jsonify(svc.cancel_clone_with_cleanup(pid))

@bp.route("/api/plugins/clone_stop_all", methods=["POST"])
def api_plugin_clone_stop_all():
    return jsonify(svc.stop_clone_and_cleanup())

@bp.route("/api/plugins/clone_cancel_queue/<int:pid>", methods=["POST"])
def api_clone_cancel_queue(pid):
    with clone_svc.clone_lock:
        if pid in clone_svc.clone_queue:
            clone_svc.clone_queue.remove(pid)
            return jsonify({"ok": True, "msg": "已从队列移除"})
    return jsonify({"ok": False, "msg": "不在队列中"})
