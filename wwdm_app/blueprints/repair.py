# -*- coding: utf-8 -*-
"""修复安装 API 蓝图（v5.0 第 6 步收尾）。

18 条：pytorch 选项、克隆 ComfyUI、分支列表/更新、依赖安装、镜像信息、
pip 安装、单插件修复、全量插件修复、杀进程等。
实现全在 services（repair_proc 有 global 重绑定，服务层不搬，只搬路由）。
"""
import os

from flask import Blueprint, jsonify, request

from .. import config
from .. import services as svc

bp = Blueprint("repair", __name__)


# ---- 修复安装 ----

@bp.route("/api/repair/pytorch_options")
def api_repair_pytorch_options():
    path = os.path.join(config.PACKAGE_DIR, "static", "info", "pytorch.txt")
    return jsonify(svc.parse_pytorch_info(path))

@bp.route("/api/repair/clone", methods=["POST"])
def api_repair_clone():
    d = request.get_json(force=True) or {}
    git_path = config.settings.get("git_path", "git")
    target_dir = (d.get("target_dir") or "").strip()
    if not target_dir:
        return jsonify({"ok": False, "msg": "请指定安装目录"})
    # 镜像统一取自全局设置中的 GitHub 镜像
    return jsonify(svc.repair_clone(git_path, target_dir))

@bp.route("/api/repair/clone_check")
def api_repair_clone_check():
    d = request.args.get("dir", "")
    return jsonify({"exists": bool(d and os.path.exists(os.path.join(d, "main.py")))})

@bp.route("/api/repair/comfyui_branches")
def api_repair_comfyui_branches():
    """分支 / tag 列表。

    ⚠️ 这里**不能**同步跑 git fetch —— 慢网络下几十秒，用户一切到本页就是个卡住的
    下拉框。服务层现在立即返回本地 ref 快照，远端刷新在后台线程里跑；响应体
    refreshing=True 时前端继续轮询本地址。?refresh=1 强制提交一次远端刷新。
    """
    git_path = config.settings.get("git_path", "git")
    comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
    force = request.args.get("refresh") in ("1", "true", "yes")
    return jsonify(svc.get_comfyui_branches(git_path, comfyui_dir, force=force))

@bp.route("/api/repair/comfyui_update", methods=["POST"])
def api_repair_comfyui_update():
    d = request.get_json(force=True) or {}
    git_path = config.settings.get("git_path", "git")
    comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
    target = (d.get("target") or "").strip()
    # 先等后台的分支刷新收工：git fetch 与 git pull 同时写 refs 会抢锁，
    # 让用户在日志里看到 "cannot lock ref" 是很差的体验。最多等 12 秒。
    svc.comfyui_git_idle()
    r = svc.repair_comfyui_update(git_path, comfyui_dir, target)
    svc.audit_result("repair.run", r, target="ComfyUI 本体更新",
                     extra="目标=%s" % (target or "当前分支"))
    return jsonify(r)

@bp.route("/api/repair/requirements", methods=["POST"])
def api_repair_requirements():
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
    r = svc.repair_requirements(python_path, comfyui_dir)
    svc.audit_result("pip.install", r, target="ComfyUI requirements.txt")
    return jsonify(r)

@bp.route("/api/repair/mirror_info")
def api_repair_mirror_info():
    """当前生效的 GitHub / pip 镜像信息（修复区域展示用）"""
    return jsonify(svc.repair_mirror_info())

@bp.route("/api/repair/pytorch", methods=["POST"])
def api_repair_pytorch():
    d = request.get_json(force=True) or {}
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    cmd_str = (d.get("cmd") or "").strip()
    r = svc.repair_pytorch(python_path, cmd_str)
    svc.audit_result("pip.install", r, target="PyTorch", extra=cmd_str[:200])
    return jsonify(r)

@bp.route("/api/repair/stop", methods=["POST"])
def api_repair_stop():
    return jsonify(svc.repair_stop())

@bp.route("/api/repair/purge_cache", methods=["POST"])
def api_repair_purge_cache():
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    r = svc.repair_purge_cache(python_path)
    svc.audit_result("repair.run", r, target="清理 pip / __pycache__ 缓存")
    return jsonify(r)

@bp.route("/api/repair/status")
def api_repair_status():
    return jsonify(svc.repair_status())

@bp.route("/api/repair/logs")
def api_repair_logs():
    since = request.args.get("since", 0, type=int)
    return jsonify(svc.repair_logs_get(since))

@bp.route("/api/repair/plugin", methods=["POST"])
def api_repair_plugin():
    d = request.get_json(force=True) or {}
    git_path = config.settings.get("git_path", "git")
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
    plugin_name = (d.get("plugin_name") or "").strip()
    if not plugin_name:
        return jsonify({"ok": False, "msg": "请填写插件名称"})
    # 镜像统一取自全局设置（GitHub 镜像 + pip 镜像源）
    r = svc.repair_single_plugin(git_path, python_path, comfyui_dir, plugin_name)
    svc.audit_result("repair.run", r, target="插件修复：%s" % plugin_name)
    return jsonify(r)

@bp.route("/api/repair/pip_install", methods=["POST"])
def api_repair_pip_install():
    d = request.get_json(force=True) or {}
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    packages = (d.get("packages") or "").strip()
    # 镜像统一取自全局设置中的 pip 镜像源
    r = svc.repair_pip_install(python_path, packages)
    svc.audit_result("pip.install", r, target=packages[:200])
    return jsonify(r)

@bp.route("/api/repair/download", methods=["POST"])
def api_repair_download():
    d = request.get_json(force=True) or {}
    aria2c = config.settings.get("aria2c_path", config.DEFAULT_SETTINGS["aria2c_path"])
    url = (d.get("url") or "").strip()
    save_path = (d.get("save_path") or "").strip()
    filename = (d.get("filename") or "").strip()
    return jsonify(svc.repair_download_file(aria2c, url, save_path, filename))

@bp.route("/api/repair/all_plugins", methods=["POST"])
def api_repair_all_plugins():
    d = request.get_json(silent=True) or {}
    git_path = config.settings.get("git_path", "git")
    python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
    comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
    # 镜像统一取自全局设置（GitHub 镜像 + pip 镜像源）
    return jsonify(svc.repair_all_plugins(git_path, python_path, comfyui_dir))

@bp.route("/api/repair/kill_comfyui", methods=["POST"])
def api_repair_kill_comfyui():
    return jsonify(svc.kill_comfyui_processes())

@bp.route("/api/repair/kill_aria2", methods=["POST"])
def api_repair_kill_aria2():
    return jsonify(svc.kill_aria2_processes())
