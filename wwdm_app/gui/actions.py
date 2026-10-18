# -*- coding: utf-8 -*-
"""gui 公共动作（v6.0(2)，v6.1 改 PyQt6）—— 悬浮球与托盘菜单共用。

为什么单独一个模块：悬浮球与托盘是两套窗口，但菜单动作完全一样（打开界面 /
复制地址 / 自启切换 / 定时关机 / 日夜间 / 退出）。放这里两边 import，
避免复制第二份、改一处漏一处。

铁律：
  * 不 import services/blueprints —— 业务走**回环 HTTP** 打自己的 API
    （``comfyui_proc`` 等 global 重绑定，非线程安全）；
  * 本模块顶层不碰 PyQt6 —— :func:`copy_url` 内部懒导入
    ``QApplication.clipboard()``，import 本包（含 pytest / 无头环境）不需要 Qt。
"""
import os

from . import autostart as _autostart


def ui_url(host, port, token="", with_token=True):
    """管理界面地址（带令牌时拼 ``?t=``）。"""
    base = "http://%s:%d" % (host, port)
    if with_token and token:
        base += "/?t=" + token
    return base


def open_ui(host, port, token=""):
    """用默认浏览器打开管理界面。失败静默（托盘/球不该因此卡死）。"""
    import webbrowser
    try:
        webbrowser.open(ui_url(host, port, token))
        return True
    except Exception:
        return False


def copy_url(host, port, token=""):
    """复制访问地址到剪贴板（PyQt6 QClipboard；无 Qt 环境返回 False）。"""
    text = ui_url(host, port, token)
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
            return True
    except Exception:
        pass
    return False


def save_flag(key, value):
    """原地改 settings 的一个键并落盘（GUI 侧专用，不走 HTTP 绕路）。

    ⚠️ 只动 GUI 自己的键（fab_visible / fab_pos / autostart）；
    业务配置一律走 ``POST /api/settings``，不在这里碰。
    """
    from ..core import config
    config.settings[key] = value
    try:
        config.save_settings(config.settings)
    except Exception:
        pass


def toggle_autostart():
    """切换开机自启：写注册表 + 存 settings。返回新值；写注册表失败返回 None。

    注册表写在这里是**用户主动点击菜单**触发的，与「POST /api/settings 不碰
    注册表」不冲突（后者是批量保存路径，测试会跑到，见 test_gui 闸门用例）。
    """
    from ..core import config
    want = not bool(config.settings.get("autostart", False))
    if not _autostart.sync(want):
        return None
    save_flag("autostart", want)
    return want


def autostart_enabled():
    """开机自启当前是否开启（settings 为准，Web 页改了就跟着变）。"""
    from ..core import config
    return bool(config.settings.get("autostart", False))


def autostart_label(flag):
    """菜单文案随当前状态反转。"""
    return "关闭开机自启动" if flag else "开机自动启动"


def http_post(host, port, path):
    """回环 POST。**必须禁代理** —— 本机 7890 等代理会转发 localhost。"""
    import json
    import urllib.request
    url = "http://%s:%d%s" % (host, port, path)
    req = urllib.request.Request(url, data=json.dumps({}).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(req, timeout=3).read()
        return True
    except Exception:
        return False


def http_json(host, port, path, method="GET", payload=None, timeout=3):
    """回环 HTTP 取 JSON（GET/POST 通用）。**必须禁代理**。

    成功返回解析后的 dict；任何失败（连不上 / 非 2xx / 非 JSON / 超时）
    返回 None —— 菜单动作拿不到数据时静默降级，不该让球/托盘卡死。
    """
    import json
    import urllib.request
    url = "http://%s:%d%s" % (host, port, path)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def schedule_shutdown(host, port, delay):
    """定时关机：回环 POST /api/shutdown。delay 单位为秒；返回后端回包 dict 或 None。"""
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = 60
    return http_json(host, port, "/api/shutdown", method="POST",
                     payload={"delay": delay})


def cancel_shutdown(host, port):
    """取消定时关机：回环 POST /api/shutdown/cancel。返回 dict 或 None。"""
    return http_json(host, port, "/api/shutdown/cancel", method="POST")


def shutdown_status(host, port):
    """查定时关机状态：回环 GET /api/shutdown/status。返回 dict 或 None。"""
    return http_json(host, port, "/api/shutdown/status", method="GET")


def set_ui_theme(host, port, theme):
    """切换网页明暗：回环 POST /api/settings {"ui_theme": theme}。

    返回切换后的主题字符串（'light' / 'dark'），失败返回 None。
    球/托盘在调用成功后自行把本地 ``config.settings['ui_theme']`` 翻成同值，
    仅供菜单文案即时翻转（真值仍以服务端落盘为准）。
    """
    if theme not in ("light", "dark"):
        theme = "light"
    r = http_json(host, port, "/api/settings", method="POST",
                 payload={"ui_theme": theme})
    if r and r.get("ok"):
        return theme
    return None


def ui_theme_label():
    """菜单文案：按当前 ui_theme 反转为下一动作（"切换到夜间"/"切换到日间"）。"""
    from ..core import config
    cur = str(config.settings.get("ui_theme", "light")).strip().lower()
    return "切换到夜间" if cur != "dark" else "切换到日间"


def quit_app(host, port):
    """退出管理器：回环让 Flask 侧 cleanup_all + os._exit。

    回环失败（服务没起来）也别把 GUI 留在桌上 —— 直接退。
    """
    if not http_post(host, port, "/api/system/quit"):
        os._exit(0)
