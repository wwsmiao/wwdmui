# -*- coding: utf-8 -*-
"""本地文件浏览 API 蓝图（v5.0 第 6 步 域 6/11）。

只放路由，不含业务逻辑；业务实现见 services/files.py。
`_scope_arg` 原先定义在 routes.py 模块级，只被这 10 条路由使用，一并搬过来。
"""
from flask import Blueprint, jsonify, request

from .. import services as svc

bp = Blueprint("files", __name__)


def _scope_arg(raw):
    """本地浏览作用域：models（ComfyUI/models）/ plugins（ComfyUI/custom_nodes）"""
    s = (raw or "").strip().lower()
    return s if s in ("models", "plugins") else "models"


# ========== 本地模型文件 API（直接操作 ComfyUI/models 目录） ==========
@bp.route("/api/models/local/list", methods=["GET"])
def api_local_list():
    return jsonify(svc.local_models_list(
        request.args.get("rel", ""), request.args.get("sort", "name_asc"),
        _scope_arg(request.args.get("scope")),
        with_stats=request.args.get("stats") == "1"))

@bp.route("/api/models/local/stats", methods=["GET"])
def api_local_stats():
    """目录递归统计（较慢，前端异步补状态栏与文件夹卡片数字）"""
    return jsonify(svc.local_dir_stats(request.args.get("rel", ""),
                                       _scope_arg(request.args.get("scope"))))

@bp.route("/api/models/local/search", methods=["GET"])
def api_local_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "msg": "请输入搜索关键字"}), 400
    sc = _scope_arg(request.args.get("scope"))
    return jsonify(svc.local_models_search(
        q, request.args.get("rel", ""), sort=request.args.get("sort", "size_desc"),
        scope=sc, include_dirs=(sc == "plugins")))

@bp.route("/api/models/local/delete", methods=["POST"])
def api_local_delete():
    d = request.get_json(force=True, silent=True) or {}
    rels = d.get("rels") or []
    if not isinstance(rels, list) or not rels:
        return jsonify({"ok": False, "msg": "未选择任何文件"}), 400
    r = svc.local_models_delete(rels, bool(d.get("permanent")),
                                _scope_arg(d.get("scope")))
    svc.audit_result("localfile.delete", r,
                     target=", ".join(str(x) for x in rels)[:200],
                     extra="永久删除" if d.get("permanent") else "移入回收站")
    return jsonify(r)

@bp.route("/api/models/local/rename", methods=["POST"])
def api_local_rename():
    d = request.get_json(force=True, silent=True) or {}
    r = svc.local_models_rename(d.get("rel", ""), d.get("new_name", ""),
                                _scope_arg(d.get("scope")))
    svc.audit_result("localfile.rename", r,
                     target=str(d.get("rel") or "")[:200],
                     extra="→ %s" % (d.get("new_name") or ""))
    return jsonify(r)

@bp.route("/api/models/local/move", methods=["POST"])
def api_local_move():
    d = request.get_json(force=True, silent=True) or {}
    rels = d.get("rels") or []
    if not isinstance(rels, list) or not rels:
        return jsonify({"ok": False, "msg": "未选择任何文件"}), 400
    r = svc.local_models_move(rels, d.get("dest", ""),
                              _scope_arg(d.get("scope")))
    svc.audit_result("localfile.move", r,
                     target=", ".join(str(x) for x in rels)[:200],
                     extra="→ %s" % (d.get("dest") or ""))
    return jsonify(r)

@bp.route("/api/models/local/move_targets", methods=["GET"])
def api_local_move_targets():
    """返回根目录下所有文件夹，供「移动到」选择"""
    return jsonify(svc.local_models_folder_tree(
        scope=_scope_arg(request.args.get("scope"))))
@bp.route("/api/models/local/open", methods=["GET"])
def api_local_open():
    return jsonify(svc.local_models_open(request.args.get("rel", ""),
                                         request.args.get("select", "0") == "1",
                                         _scope_arg(request.args.get("scope"))))

@bp.route("/api/models/local/upload", methods=["POST"])
def api_local_upload():
    """上传单个或多个文件到本地模型 / 插件目录（multipart/form-data：files, dest, scope）"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "msg": "未选择文件"}), 400
    r = svc.local_models_upload((request.form.get("dest") or "").strip(),
                                files, _scope_arg(request.form.get("scope")),
                                request.form.getlist("paths"))
    svc.audit_result("localfile.upload", r,
                     target=", ".join((f.filename or "") for f in files)[:200],
                     extra="→ %s" % (request.form.get("dest") or ""))
    return jsonify(r)

@bp.route("/api/models/local/mkdir", methods=["POST"])
def api_local_mkdir():
    """在本地模型 / 插件目录下新建文件夹（JSON：rel, name, scope）"""
    d = request.get_json(silent=True) or {}
    r = svc.local_models_mkdir(d.get("rel", ""), d.get("name", ""),
                               _scope_arg(d.get("scope")))
    svc.audit_result("localfile.mkdir", r,
                     target=str(d.get("rel") or "")[:200],
                     extra="→ %s" % (d.get("name") or ""))
    return jsonify(r)
