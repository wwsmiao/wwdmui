# -*- coding: utf-8 -*-
"""v6.0 桌面入口层：悬浮球（托盘图标留待后续验证后补，见文末）。

依赖方向：``gui → core``（config/logging），**不 import services/blueprints** ——
保持 core 铁律「基础层不许被 GUI 反向依赖业务」的镜像版：GUI 层也不许依赖业务层。

铁律（fab.py 同）：
  * tkinter 只在**主线程**创建/操作，Flask 由 run.py 放子线程；
  * 悬浮球**不直接调 services**（``comfyui_proc`` 等有 global 重绑定，非线程安全），
    一切业务动作走回环 HTTP 打自己的 API；
  * 一切按钮动作最终都经 ``root.after(0, fn)`` 回主线程执行。

为什么先不做托盘：Windows 托盘要 ``Shell_NotifyIcon`` + 自建消息循环
（ctypes 手写 NOTIFYICONDATAW），零依赖但风险高；悬浮球默认常驻已有入口，
需要「关掉球也能找回」时再补不迟（Web 设置页可恢复 fab_visible）。
"""
import os
import sys

from . import autostart, pos  # noqa: F401  （pos/autostart 的纯函数从这里再导出）
from . import fab  # noqa: F401  （fab 顶层不碰 tkinter，只在 start() 内 import）


def should_run_gui(argv=None, env=None, has_tk=None):
    """本次启动是否该走 GUI（悬浮球）路径。

    判定顺序（任一为否则降级为纯 Flask 模式，开发/服务器/CI 都走这条）：
      1. ``--no-gui`` 参数或 ``WWDMUI_NO_GUI=1`` 环境变量；
      2. import tkinter 失败（精简 Python / venv 没装 Tk）；
      3. 无显示环境（Windows 会话 0 服务里跑、SSH 无 DISPLAY）。
    参数可注入，便于测试。
    """
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if "--no-gui" in argv:
        return False
    if str(env.get("WWDMUI_NO_GUI", "")).strip() in ("1", "true", "yes"):
        return False
    if has_tk is None:
        if not _tkinter_available():
            return False
    elif not has_tk:
        return False
    return _has_display()


def _tkinter_available():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _has_display():
    """Windows 下会话 0（服务/计划任务无交互会话）没有桌面，建窗会炸。"""
    if os.name != "nt":
        return bool(os.environ.get("DISPLAY"))
    try:
        import ctypes
        # 会话 0 没有 shell 窗口；正常桌面环境 GetShellWindow 必返回非 0。
        # 比 ProcessIdToSessionId 少一次调用，且覆盖「服务里跑」的全部形态。
        return bool(ctypes.windll.user32.GetShellWindow())
    except Exception:
        # 连 user32 都摸不到基本不可能是正常桌面环境；SESSIONNAME 兜底
        return os.environ.get("SESSIONNAME", "") not in ("", "Services")


def save_fab_pos(x, y):
    """保存悬浮球位置（同进程原地更新 + 落盘，不做 HTTP 绕路）。

    GUI 线程只动 ``settings`` 字典的一个键（原子），保存走既有
    ``config.save_settings``。位置合法性已由 :mod:`gui.pos` 在调用前保证。
    """
    from ..core import config
    config.settings["fab_pos"] = pos.fmt_pos(x, y)
    try:
        config.save_settings(config.settings)
    except Exception:
        pass
