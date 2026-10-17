# -*- coding: utf-8 -*-
"""输出管理 API 蓝图（v5.0 第 6 步 域 8/11）。

只放路由，不含业务逻辑；业务实现见 services/output.py。
`_get_output_dir` 原为 routes.py 内 register() 的嵌套辅助，一并搬来。
"""
import os
import subprocess

from flask import Blueprint, jsonify, request, send_file

from .. import config
from .. import services as svc
from ..routes import _zip_response, _get_input_dir

bp = Blueprint("output", __name__)


# ========== 输出管理 API ==========
def _get_output_dir():
    d = config.settings.get("output_dir", "").strip()
    if d:
        return d
    comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
    return os.path.join(comfy, "output")

@bp.route("/api/output/browse")
def api_output_browse():
    root = _get_output_dir()
    rel = request.args.get("path", "")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    search = request.args.get("search", "")
    sort = request.args.get("sort", "date_desc")
    return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

@bp.route("/api/output/file")
def api_output_file():
    root = _get_output_dir()
    rel = request.args.get("path", "")
    file_path = svc.serve_output_file(root, rel)
    if not file_path:
        return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
    return send_file(file_path)

@bp.route("/api/output/download")
def api_output_download():
    """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
    root = _get_output_dir()
    paths = request.args.get("paths", "")
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    rel_paths = [p for p in paths.split(",") if p]
    if not rel_paths:
        return jsonify({"ok": False, "msg": "未指定路径"}), 400
    # 单个文件：直接附件下载
    if len(rel_paths) == 1:
        fp = svc.serve_output_file(root, rel_paths[0])
        # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
        # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
        if fp and os.path.isfile(fp):
            return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
    # 多选/文件夹：打包 zip
    items, gen = svc.zip_files_stream(root, rel_paths, "output.zip")
    if not items:
        return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
    return _zip_response(gen, "output.zip")

@bp.route("/api/output/open_dir")
def api_output_open_dir():
    """在资源管理器中打开输出目录"""
    d = _get_output_dir()
    if os.path.isdir(d):
        try:
            # explorer <path> 比 os.startfile 更可靠地前置窗口
            subprocess.Popen(['explorer', d], shell=False)
        except Exception:
            os.startfile(d)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "目录不存在: " + d})

@bp.route("/api/output/delete", methods=["POST"])
def api_output_delete():
    root = _get_output_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"})
    return jsonify(svc.delete_output_files(root, paths))

@bp.route("/api/output/rename", methods=["POST"])
def api_output_rename():
    root = _get_output_dir()
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    new_name = data.get("new_name", "")
    if not path or not new_name:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.rename_output_file(root, path, new_name))

@bp.route("/api/output/move", methods=["POST"])
def api_output_move():
    root = _get_output_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.move_output_files(root, paths, dest))

@bp.route("/api/output/copy", methods=["POST"])
def api_output_copy():
    root = _get_output_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    dest = data.get("dest", "")
    if not paths or dest is None:
        return jsonify({"ok": False, "msg": "缺少参数"})
    return jsonify(svc.copy_output_files(root, paths, dest))

@bp.route("/api/output/send_to_input", methods=["POST"])
def api_output_send_to_input():
    """将选中的输出文件发送到输入文件夹"""
    root_out = _get_output_dir()
    root_in = _get_input_dir()
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    if not paths:
        return jsonify({"ok": False, "msg": "未指定路径"})
    return jsonify(svc.send_to_input_dir(root_out, paths, root_in))

@bp.route("/api/output/mkdir", methods=["POST"])
def api_output_mkdir():
    root = _get_output_dir()
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "msg": "缺少路径"})
    return jsonify(svc.create_output_dir(root, path))

@bp.route("/api/output/upload", methods=["POST"])
def api_output_upload():
    root = _get_output_dir()
    dest = (request.form.get("dest") or "").strip()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"ok": False, "msg": "未选择文件"})
    return jsonify(svc.upload_output_files(root, dest, files,
                                           request.form.getlist("paths")))
