# -*- coding: utf-8 -*-
"""工具检测 + GitHub 镜像 API 蓝图（v5.0 第 6 步收尾）。

两件事都是「问外部环境一句」：git/python 在哪、什么版本；GitHub 镜像哪些通。
纯路由，实现全在 services。
"""
import os
import subprocess
import platform as _platform

from flask import Blueprint, jsonify

from .. import config
from .. import services as svc
from ..routes import _tool_version_cached

bp = Blueprint("tools", __name__)


# ========== 工具检测 API ==========
@bp.route("/api/git/detect", methods=["GET"])
def api_git_detect():
    path = svc.get_git_path()
    ver = _tool_version_cached("git:" + str(path), [path, "--version"])
    found = ver is not None
    if not found and not config.IS_WINDOWS and path == "git":
        try:
            rr = subprocess.run(["which", "git"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
            if rr.returncode == 0:
                path = rr.stdout.strip()
                ver = _tool_version_cached("git:" + str(path), [path, "--version"])
                found = ver is not None
        except Exception:
            pass
    return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

@bp.route("/api/python/detect", methods=["GET"])
def api_python_detect():
    path = svc.get_python_path()
    ver = _tool_version_cached("py:" + str(path), [path, "--version"])
    found = ver is not None
    if not found and not config.IS_WINDOWS and path == "python3":
        for alt in ["python3", "python"]:
            try:
                rr = subprocess.run(["which", alt], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                if rr.returncode == 0:
                    path = rr.stdout.strip()
                    ver = _tool_version_cached("py:" + str(path), [path, "--version"])
                    if ver is not None:
                        found = True
                        break
            except Exception:
                pass
    return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

# ========== GitHub 镜像 API ==========
@bp.route("/api/github/mirrors")
def api_github_mirrors():
    """返回可选的 GitHub 镜像列表与当前选择"""
    return jsonify({"ok": True, "mirrors": svc.GITHUB_MIRRORS,
                    "current": config.settings.get("github_mirror", "official")})

@bp.route("/api/github/mirror_test")
def api_github_mirror_test():
    """并发测试各 GitHub 镜像连通性（探测 git-upload-pack 端点）"""
    return jsonify(svc.test_github_mirrors())
