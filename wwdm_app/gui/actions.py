# -*- coding: utf-8 -*-
"""gui 公共动作（v6.0(2)）—— 悬浮球与托盘菜单共用。

为什么单独一个模块：托盘（ctypes 消息窗）与悬浮球（tkinter 窗）是两套窗口
机制，但菜单动作完全一样（打开界面 / 复制地址 / 自启切换 / 退出）。放这里
两边 import，避免复制第二份、改一处漏一处。

铁律（同 fab.py）：
  * 不 import services/blueprints —— 业务走**回环 HTTP** 打自己的 API
    （``comfyui_proc`` 等 global 重绑定，非线程安全）；
  * 本模块顶层不碰 tkinter —— :func:`copy_url` 的 root 由调用方传入，
    import 本包（含 pytest / 无头环境）不需要 Tk。
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


def copy_url(root, host, port, token=""):
    """复制访问地址到剪贴板。root 为 tkinter 根；None 时跳过（单测用）。"""
    if root is None:
        return False
    try:
        root.clipboard_clear()
        root.clipboard_append(ui_url(host, port, token))
        return True
    except Exception:
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


def quit_app(host, port):
    """退出管理器：回环让 Flask 侧 cleanup_all + os._exit。

    回环失败（服务没起来）也别把 GUI 留在桌上 —— 直接退。
    """
    if not http_post(host, port, "/api/system/quit"):
        os._exit(0)
