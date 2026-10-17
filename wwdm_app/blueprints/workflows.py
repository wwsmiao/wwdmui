# -*- coding: utf-8 -*-
"""工作流 API 蓝图（v5.0 第 6 步 域 7/11）。

只放路由，不含业务逻辑；业务实现见 services/workflows.py。
`_get_workflow_dir` 原为 routes.py 内 register() 的嵌套辅助，一并搬来。
`_zip_response` 仍住在 routes.py 模块级（通用工具），等 routes.py 彻底清空时再归位。
"""
import os
import subprocess

from flask import Blueprint, jsonify, request, send_file

from .. import config
from .. import services as svc
from ..routes import _zip_response

bp = Blueprint("workflows", __name__)


# ========== 工作流管理 API ==========
def _get_workflow_dir():
    d = config.settings.get("workflow_dir", "").strip()
    if d:
        return d
    comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
    return os.path.join(comfy, "user", "default", "workflows")

@bp.route("/api/workflows/browse")
def api_workflows_browse():
    root = _get_workflow_dir()
    rel = request.args.get("path", "")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    search = request.args.get("search", "")
    sort = request.args.get("sort", "date_desc")
    return jsonify(svc.workflows_browse(root, rel, page, page_size, search, sort))

@bp.route("/api/workflows/get")
def api_workflows_get():
    root = _get_workflow_dir()
    rel = request.args.get("path", "")
    return jsonify(svc.workflows_get(root, rel))

@bp.route("/api/workflows/save", methods=["POST"])
def api_workflows_save():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    content = data.get("content", "")
    if not rel:
        return jsonify({"ok": False, "msg": "缺少路径"})
    return jsonify(svc.workflows_save(root, rel, content))

@bp.route("/api/workflows/new", methods=["POST"])
def api_workflows_new():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    if not name:
        return jsonify({"ok": False, "msg": "缺少文件名"})
    return jsonify(svc.workflows_new(root, name))

@bp.route("/api/workflows/delete", methods=["POST"])
def api_workflows_delete():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"})
    return jsonify(svc.delete_output_files(root, paths))

@bp.route("/api/workflows/rename", methods=["POST"])
def api_workflows_rename():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    new_name = data.get("new_name", "")
    if not rel or not new_name:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.rename_output_file(root, rel, new_name))

@bp.route("/api/workflows/move", methods=["POST"])
def api_workflows_move():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.workflows_move_files(root, paths, dest))

@bp.route("/api/workflows/copy", methods=["POST"])
def api_workflows_copy():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.workflows_copy_files(root, paths, dest))

@bp.route("/api/workflows/mkdir", methods=["POST"])
def api_workflows_mkdir():
    root = _get_workflow_dir()
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "msg": "缺少路径"})
    return jsonify(svc.create_output_dir(root, path))

@bp.route("/api/workflows/upload", methods=["POST"])
def api_workflows_upload():
    """上传单个或多个文件/整个文件夹到工作流目录（multipart：files, dest, paths[]）"""
    root = _get_workflow_dir()
    dest = (request.form.get("dest") or "").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "msg": "未选择文件"})
    return jsonify(svc.upload_output_files(root, dest, files,
                                           request.form.getlist("paths")))

@bp.route("/api/workflows/file")
def api_workflows_file():
    root = _get_workflow_dir()
    rel = request.args.get("path", "")
    file_path = svc.serve_workflow_file(root, rel)
    if not file_path:
        return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
    return send_file(file_path, as_attachment=True)

@bp.route("/api/workflows/download")
def api_workflows_download():
    """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
    root = _get_workflow_dir()
    paths = request.args.get("paths", "")
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    rel_paths = [p for p in paths.split(",") if p]
    if not rel_paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    if len(rel_paths) == 1:
        fp = svc.serve_workflow_file(root, rel_paths[0])
        # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
        # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
        if fp and os.path.isfile(fp):
            return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
    items, gen = svc.zip_files_stream(root, rel_paths, "workflows.zip")
    if not items:
        return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
    return _zip_response(gen, "workflows.zip")

@bp.route("/api/workflows/backup", methods=["POST"])
def api_workflows_backup():
    root = _get_workflow_dir()
    backup_dir = config.settings.get("workflow_backup_dir", "").strip()
    if not backup_dir:
        return jsonify({"ok": False, "msg": "未配置备份目录，请在全局设置中填写"})
    return jsonify(svc.workflows_backup(root, backup_dir))

@bp.route("/api/workflows/open_dir")
def api_workflows_open_dir():
    d = _get_workflow_dir()
    if os.path.isdir(d):
        try:
            subprocess.Popen(['explorer', d], shell=False)
        except Exception:
            os.startfile(d)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "目录不存在: " + d})


@bp.route("/api/workflows/analyze")
def api_workflows_analyze():
    root = _get_workflow_dir()
    rel = request.args.get("path", "")
    if not rel:
        return jsonify({"ok": False, "msg": "缺少路径"})
    return jsonify(svc.workflows_analyze(root, rel))
