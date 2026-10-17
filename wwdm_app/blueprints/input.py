# -*- coding: utf-8 -*-
"""输入目录管理 API 蓝图（v5.0 第 6 步 域 9/11）。

输入管理与输出管理是**同一套逻辑换根目录**，业务实现直接复用 services/output.py，
本文件只解析 `input_dir` 再转调，不含任何业务实现。
`_get_input_dir` 原为 routes.py 内 register() 的嵌套辅助，已提到 routes.py 模块级。
"""
import os
import subprocess

from flask import Blueprint, jsonify, request, send_file

from .. import services as svc
from ..routes import _zip_response, _get_input_dir

bp = Blueprint("input", __name__)


# ========== 输入管理 API ==========
@bp.route("/api/input/browse")
def api_input_browse():
    root = _get_input_dir()
    rel = request.args.get("path", "")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    search = request.args.get("search", "")
    sort = request.args.get("sort", "date_desc")
    return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

@bp.route("/api/input/file")
def api_input_file():
    root = _get_input_dir()
    rel = request.args.get("path", "")
    file_path = svc.serve_output_file(root, rel)
    if not file_path:
        return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
    return send_file(file_path)

@bp.route("/api/input/download")
def api_input_download():
    """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
    root = _get_input_dir()
    paths = request.args.get("paths", "")
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    rel_paths = [p for p in paths.split(",") if p]
    if not rel_paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    if len(rel_paths) == 1:
        fp = svc.serve_output_file(root, rel_paths[0])
        # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
        # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
        if fp and os.path.isfile(fp):
            return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
    items, gen = svc.zip_files_stream(root, rel_paths, "input.zip")
    if not items:
        return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
    return _zip_response(gen, "input.zip")

@bp.route("/api/input/open_dir")
def api_input_open_dir():
    d = _get_input_dir()
    if os.path.isdir(d):
        try:
            subprocess.Popen(['explorer', d], shell=False)
        except Exception:
            os.startfile(d)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "目录不存在: " + d})

@bp.route("/api/input/delete", methods=["POST"])
def api_input_delete():
    root = _get_input_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"})
    return jsonify(svc.delete_output_files(root, paths))

@bp.route("/api/input/rename", methods=["POST"])
def api_input_rename():
    root = _get_input_dir()
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    new_name = data.get("new_name", "")
    if not path or not new_name:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.rename_output_file(root, path, new_name))

@bp.route("/api/input/move", methods=["POST"])
def api_input_move():
    root = _get_input_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.move_output_files(root, paths, dest))

@bp.route("/api/input/copy", methods=["POST"])
def api_input_copy():
    root = _get_input_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.copy_output_files(root, paths, dest))

@bp.route("/api/input/mkdir", methods=["POST"])
def api_input_mkdir():
    root = _get_input_dir()
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "msg": "缺少路径"})
    return jsonify(svc.create_output_dir(root, path))

@bp.route("/api/input/upload", methods=["POST"])
def api_input_upload():
    root = _get_input_dir()
    dest = (request.form.get("dest") or "").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "msg": "未选择文件"})
    return jsonify(svc.upload_output_files(root, dest, files,
                                           request.form.getlist("paths")))
