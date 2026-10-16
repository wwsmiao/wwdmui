# -*- coding: utf-8 -*-
"""抖音图文 / 视频解析与下载路由（v5.0 第 6 步 · 域 1/10）。

从 ``routes.py`` 的 ``register(app)`` 里整段搬来，**只是把 ``@app.route``
换成 ``@bp.route`` 并去掉一级缩进**，路由规则（URL / 方法）逐字未变。

⚠️ 两处必须留意的搬迁差异：
1. ``_douyin_cookie_file()`` 的 dy_cookie.json 兜底路径 —— 原文件在
   ``wwdm_app/``，本文件在 ``wwdm_app/blueprints/``，所以 ``__file__`` 要**多退一级**。
2. 端点名从 ``api_douyin_fetch`` 变成 ``douyin.api_douyin_fetch``。
   本项目**全仓没有 ``url_for``**（已 grep 确认），所以改名无影响。
"""
import os
import subprocess

from flask import Blueprint, jsonify, request

from .. import config
from .. import services as svc

bp = Blueprint("douyin", __name__)


# ========== 抖音提取 API ==========
def _douyin_cookie_file():
    """获取抖音 Cookie 文件路径"""
    f = config.settings.get("douyin_cookie_file", "").strip()
    if f and os.path.isfile(f):
        return f
    # fallback: 项目目录下 dy_cookie.json
    fallback = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "dy_cookie.json")
    if os.path.isfile(fallback):
        return fallback
    return None

@bp.route("/api/douyin/fetch", methods=["POST"])
def api_douyin_fetch():
    """提取抖音链接的图片/视频信息（使用 dy_cookie.json）"""
    data = request.get_json(silent=True) or {}
    raw_text = (data.get("url") or data.get("text") or "").strip()
    if not raw_text:
        return jsonify({"ok": False, "msg": "请输入抖音链接或包含链接的文本"})
    cookie_file = _douyin_cookie_file()
    if not cookie_file:
        return jsonify({"ok": False, "msg": "请先在全局设置中配置 dy_cookie.json 路径（Cookie 文件格式：原始 Cookie 字符串或 JSON）"})
    return jsonify(svc.douyin_fetch(raw_text, cookie_file))

@bp.route("/api/douyin/download", methods=["POST"])
def api_douyin_download():
    """通过 aria2c 下载图片或视频"""
    data = request.get_json(silent=True) or {}
    save_dir = (data.get("save_dir") or "").strip()
    if not save_dir:
        save_dir = config.settings.get("douyin_save_dir", "").strip()
    if not save_dir:
        return jsonify({"ok": False, "msg": "请配置保存目录"})

    # 视频下载
    video_url = (data.get("video_url") or "").strip()
    if video_url:
        name = (data.get("video_name") or data.get("aweme_id") or "video").strip() + ".mp4"
        cookie_file = _douyin_cookie_file()
        return jsonify(svc.douyin_download_video(video_url, save_dir, name, cookie_file))

    # 图片下载
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"ok": False, "msg": "没有可下载的图片"})
    tmpl = (data.get("name_template") or "{index:02d}.{ext}").strip()
    cookie_file = _douyin_cookie_file()
    return jsonify(svc.douyin_download_images(urls, save_dir, tmpl, cookie_file))


@bp.route("/api/douyin/progress/<gid>")
def api_douyin_progress(gid):
    return jsonify(svc.douyin_download_status(gid))


@bp.route("/api/douyin/open_dir")
def api_douyin_open_dir():
    d = config.settings.get("douyin_save_dir", "").strip()
    if d and os.path.isdir(d):
        try:
            subprocess.Popen(['explorer', d], shell=False)
        except Exception:
            os.startfile(d)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "目录不存在或未配置"})
