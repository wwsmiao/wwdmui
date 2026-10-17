# -*- coding: utf-8 -*-
"""ComfyUI 启动器 API 蓝图（v5.0 第 6 步收尾）。

启动/停止/状态/日志/命令预览与重置。实现全在 services（comfyui_proc 有 global
重绑定，服务层不搬，只搬路由）。
"""
from flask import Blueprint, jsonify, request

from .. import config
from .. import services as svc

bp = Blueprint("comfyui", __name__)


# ========== ComfyUI 启动器 API ==========
@bp.route("/api/comfyui/start", methods=["POST"])
def api_comfyui_start():
    s = config.settings  # 实时读取
    python_path = s.get("python_path") or config.DEFAULT_SETTINGS["python_path"]
    comfyui_dir = s.get("comfyui_dir") or config.DEFAULT_SETTINGS["comfyui_dir"]
    r = svc.start_comfyui(python_path, comfyui_dir)
    svc.audit_result("comfyui.start", r, target=comfyui_dir)
    return jsonify(r)

@bp.route("/api/comfyui/stop", methods=["POST"])
def api_comfyui_stop():
    r = svc.stop_comfyui()
    svc.audit_result("comfyui.stop", r)
    return jsonify(r)

@bp.route("/api/comfyui/status")
def api_comfyui_status():
    return jsonify(svc.get_comfyui_status())

@bp.route("/api/comfyui/logs")
def api_comfyui_logs():
    since = request.args.get("since", 0, type=int)
    return jsonify(svc.get_comfyui_logs(since))

@bp.route("/api/comfyui/cmd_preview")
def api_comfyui_cmd_preview():
    s = config.settings
    py = s.get("python_path") or config.DEFAULT_SETTINGS["python_path"]
    d = s.get("comfyui_dir") or config.DEFAULT_SETTINGS["comfyui_dir"]
    cmd = svc.build_comfyui_cmd(py, d)
    prefix = svc._build_preset_env_prefix()
    cmd_str = (prefix or "") + " ".join(cmd)
    return jsonify({"cmd": cmd, "cmd_str": cmd_str})

@bp.route("/api/comfyui/cmd_reset", methods=["POST"])
def api_comfyui_cmd_reset():
    config.settings["comfyui_listen"] = config.DEFAULT_SETTINGS["comfyui_listen"]
    config.settings["comfyui_port"] = config.DEFAULT_SETTINGS["comfyui_port"]
    config.settings["comfyui_auto_launch"] = config.DEFAULT_SETTINGS["comfyui_auto_launch"]
    config.settings["comfyui_vram"] = config.DEFAULT_SETTINGS["comfyui_vram"]
    config.settings["comfyui_preview_method"] = config.DEFAULT_SETTINGS["comfyui_preview_method"]
    config.settings["comfyui_cors_origin"] = config.DEFAULT_SETTINGS["comfyui_cors_origin"]
    config.settings["comfyui_extra_args"] = config.DEFAULT_SETTINGS["comfyui_extra_args"]
    config.settings["comfyui_presets"] = config.DEFAULT_SETTINGS["comfyui_presets"]
    config.save_settings(config.settings)
    return jsonify({"ok": True, "msg": "ComfyUI 启动命令已重置为默认"})
