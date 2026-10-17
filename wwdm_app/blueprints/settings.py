# -*- coding: utf-8 -*-
"""设置 / 系统 API 蓝图（v5.0 第 6 步收尾）。

包含全局设置读写与重置、访问令牌重生成、壁纸文件投递、退出管理器。
⚠️ 改动设置时必须**原地** clear+update config.settings（见 api_reset_settings 注释）。
"""
import os
import time
import threading

from flask import Blueprint, jsonify, request, send_file

from .. import config
from .. import security as sec
from .. import services as svc

bp = Blueprint("settings", __name__)


# ========== 设置 API ==========
@bp.route("/api/settings", methods=["GET"])
def api_get_settings():
    data = dict(config.settings)
    rt = dict(getattr(config, "RUNTIME_INFO", None) or {})
    eff_host = str(rt.get("host") or "").strip()
    try:
        eff_port = int(rt.get("port") or 0)
    except (ValueError, TypeError):
        eff_port = 0
    # _effective = 「本进程启动时真实监听」的值（不是 settings 里的值）。
    # 前端拿它和表单里的值比，才知道是否有改动待重启。
    data["_effective"] = {
        "host": eff_host,
        "port": eff_port,
        "started_at": rt.get("started_at") or 0,
        "token_enabled": bool(rt.get("token_enabled")),
        "lan_ips": list(rt.get("lan_ips") or []),
        "running": bool(eff_host and eff_port),
    }
    data["_client"] = {
        "ip": request.remote_addr or "",
        "host": request.host or "",
        "local": sec.is_loopback(request.remote_addr),
    }
    return jsonify(data)

@bp.route("/api/settings", methods=["POST"])
def api_save_settings():
    d = request.get_json(silent=True)
    if not d:
        return jsonify({"ok": False, "msg": "无效数据"}), 400
    try:
        d["max_concurrent_downloads"] = max(1, min(10, int(d.get("max_concurrent_downloads", 3))))
        d["max_concurrent_clones"] = max(1, min(5, int(d.get("max_concurrent_clones", 2))))
        d["max_connection_per_server"] = max(1, min(16, int(d.get("max_connection_per_server", 8))))
        d["split"] = max(1, min(16, int(d.get("split", 8))))
        d["connect_timeout"] = max(10, int(d.get("connect_timeout", 60)))
        d["timeout"] = max(10, int(d.get("timeout", 60)))
        d["retry_wait"] = max(1, int(d.get("retry_wait", 3)))
        d["max_tries"] = max(0, int(d.get("max_tries", 0)))
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "msg": "参数错误: " + str(e)}), 400
    if not d.get("python_path"):
        d["python_path"] = config.DEFAULT_SETTINGS["python_path"]
    if not d.get("aria2c_path"):
        d["aria2c_path"] = config.DEFAULT_SETTINGS["aria2c_path"]
    if not d.get("comfyui_dir"):
        d["comfyui_dir"] = config.DEFAULT_SETTINGS["comfyui_dir"]
    if not d.get("git_path"):
        d["git_path"] = config.DEFAULT_SETTINGS["git_path"]
    d["check_certificate"] = bool(d.get("check_certificate", False))
    d["auto_start_aria2"] = bool(d.get("auto_start_aria2", True))
    # comfyui 启动参数：字符串直接保留
    for k in ("comfyui_listen", "comfyui_port", "comfyui_vram",
              "comfyui_preview_method", "comfyui_cors_origin", "comfyui_extra_args"):
        d[k] = str(d.get(k, config.settings.get(k, ""))).strip()
    d["comfyui_auto_launch"] = bool(d.get("comfyui_auto_launch", True))
    # comfyui 环境变量预设
    raw_presets = d.get("comfyui_presets", config.settings.get("comfyui_presets", []))
    if isinstance(raw_presets, list):
        d["comfyui_presets"] = [
            {"key": (p.get("key", "") or "").strip(), "value": (p.get("value", "") or "").strip()}
            for p in raw_presets if isinstance(p, dict) and (p.get("key", "") or "").strip()
        ]
    else:
        d["comfyui_presets"] = config.settings.get("comfyui_presets", [])
    # 壁纸毛玻璃效果 clamp 到 0-10 整数（0=无玻璃，1-10=像素）
    try:
        wb = int(round(float(d.get("wallpaper_blur", config.settings.get("wallpaper_blur", 0)))))
        d["wallpaper_blur"] = max(0, min(10, wb))
    except (ValueError, TypeError):
        d["wallpaper_blur"] = 0
    d["wallpaper_path"] = (d.get("wallpaper_path", "") or "").strip()
    # GitHub 镜像：仅接受已知 key，非法值回退官方
    _gm = str(d.get("github_mirror", config.settings.get("github_mirror", "official")) or "official").strip()
    d["github_mirror"] = _gm if _gm in svc._GITHUB_MIRROR_MAP else "official"
    # ---- HuggingFace 渠道（v5.1）----
    _hfc = str(d.get("hf_channel", config.settings.get("hf_channel", "hf-mirror")) or "hf-mirror").strip()
    d["hf_channel"] = _hfc if svc.hf_channel_known(_hfc) else "hf-mirror"
    d["hf_env_inject"] = bool(d.get("hf_env_inject",
                                    config.settings.get("hf_env_inject", True)))
    # 按「渠道 + 是否注入」自动维护 comfyui_presets 里的 HF_ENDPOINT 条目 ——
    # 不让用户手输 key/value：HF_ENDPOINT 拼错一个字母会静默无效。
    # 用户手动加的同名条目会被这一条覆盖（同键后者生效，本就是既有语义）。
    _presets = [p for p in (d.get("comfyui_presets") or [])
                if (p.get("key") or "").strip() != svc.HF_ENV_KEY]
    _hf_entry = svc.hf_preset_entry_for(d["hf_channel"]) if d["hf_env_inject"] else None
    if _hf_entry:
        _presets.append(_hf_entry)
    d["comfyui_presets"] = _presets
    # ---- 管理器访问 ----
    _wh = str(d.get("web_host", config.settings.get("web_host", "0.0.0.0")) or "").strip()
    d["web_host"] = _wh if _wh in ("0.0.0.0", "127.0.0.1") else "0.0.0.0"
    try:
        _wp = int(d.get("web_port", config.settings.get("web_port", 7860)) or 7860)
    except (ValueError, TypeError):
        _wp = 7860
    d["web_port"] = _wp if 1024 <= _wp <= 65535 else 7860
    d["token_required"] = bool(d.get("token_required", True))
    try:
        _mu = float(d.get("max_upload_gb", config.settings.get("max_upload_gb", 8)) or 8)
    except (ValueError, TypeError):
        _mu = 8.0
    d["max_upload_gb"] = max(0.1, min(64.0, _mu))
    # 令牌有专门的「重新生成」入口，这里只做兜底：前端没带就沿用旧值，绝不允许被清空
    _tk = str(d.get("access_token", "") or "").strip()
    d["access_token"] = _tk or (config.settings.get("access_token") or "")
    if not d["access_token"]:
        d["access_token"] = config.new_access_token()
    config.settings.update(d)
    config.save_settings(config.settings)
    rt = getattr(config, "RUNTIME_INFO", None) or {}
    _cur_host = str(rt.get("host") or "").strip()
    try:
        _cur_port = int(rt.get("port") or 0)
    except (ValueError, TypeError):
        _cur_port = 0
    need_restart = bool(_cur_port) and (d["web_host"] != _cur_host
                                       or int(d["web_port"]) != _cur_port)
    msg = "设置已保存"
    if need_restart:
        msg += "；监听地址/端口有改动，需要重启管理器才生效"
    else:
        msg += "，重启 aria2c 生效"
    # 审计：把这次改动的渠道类设置写进 detail —— 「为啥下载走官方了」
    # 这类问题以后直接翻审计日志就有答案，不用猜。
    svc.audit("settings.save",
              detail="HF 渠道=%s · GitHub 镜像=%s · pip 源=%s · 主机=%s:%s"
                     % (d.get("hf_channel"), d.get("github_mirror"),
                        d.get("pip_mirror"), d.get("web_host"), d.get("web_port")))
    return jsonify({"ok": True, "msg": msg, "need_restart": need_restart})

@bp.route("/api/settings/reset", methods=["POST"])
def api_reset_settings():
    # 访问令牌要保留：重置设置不应该把已经开启的访问保护「悄悄关掉」
    keep_tok = str(config.settings.get("access_token") or "").strip()
    # ⚠️ 必须【原地】清空再填充，不能写成 config.settings = dict(DEFAULT_SETTINGS)：
    # services.py 是 `from .config import settings`（import 时就绑定的同一个 dict 对象），
    # 重新赋值只换掉 config 这边的引用，services 仍然指向旧字典 —— 于是
    # 「恢复默认设置」在界面上看着生效了，后端却还在用旧的 comfyui_dir / python_path /
    # 镜像 / 并发，直到重启。实测：重新赋值后 services.get_comfyui_dir() 仍是旧值。
    # （单用户管理动作，这里不做并发保护；读取方都是 .get(k, 默认)，短窗口不会崩。）
    fresh = dict(config.DEFAULT_SETTINGS)
    fresh["access_token"] = keep_tok or config.new_access_token()
    config.settings.clear()
    config.settings.update(fresh)
    config.save_settings(config.settings)
    svc.audit("settings.reset", detail="已恢复默认设置（访问令牌已保留）")
    return jsonify({"ok": True, "msg": "已恢复默认设置（访问令牌已保留）"})

@bp.route("/api/settings/regenerate_token", methods=["POST"])
def api_regenerate_token():
    """重新生成访问令牌，立即生效（旧令牌与其 Cookie 立刻失效）"""
    tok = config.new_access_token()
    config.settings["access_token"] = tok
    config.save_settings(config.settings)
    try:
        sec._LOG.warning("访问令牌已重新生成")
    except Exception:
        pass
    svc.audit("token.regenerate", detail="旧令牌立即失效，已登录的其它设备需重新输入")
    return jsonify({"ok": True, "access_token": tok,
                    "msg": "已生成新的访问令牌并立即生效，已登录的其它设备需要重新输入"})

@bp.route("/api/system/quit", methods=["POST"])
def api_system_quit():
    """退出管理器（只退出，不自动拉起新进程）。
    说明：Windows 下 Werkzeug 开了 SO_REUSEADDR，新进程会和旧进程同时 LISTEN 同一端口，
    先绑定者吃流量 —— 所以这里不做「一键重启」，只做干净退出，由用户重新双击启动器。"""
    def _die():
        time.sleep(0.8)
        try:
            svc.cleanup_all()
        except Exception:
            pass
        os._exit(0)
    svc.audit("system.quit", detail="管理器退出（不自动拉起新进程）")
    threading.Thread(target=_die, daemon=True).start()
    return jsonify({"ok": True, "msg": "管理器正在退出，请重新双击启动器打开"})

@bp.route("/api/wallpaper")
def api_wallpaper():
    """提供背景壁纸文件（图片或 mp4）。路径由用户在全局设置中配置。"""
    wp = config.settings.get("wallpaper_path", "").strip()
    if not wp or not os.path.isfile(wp):
        return jsonify({"ok": False, "msg": "壁纸不存在"}), 404
    return send_file(wp)
