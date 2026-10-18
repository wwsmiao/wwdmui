# -*- coding: utf-8 -*-
"""v6.1 桌面入口层：**PyQt6** 悬浮球 + 系统托盘（取代 tkinter / ctypes 旧实现）。

依赖方向：``gui → core``（config/logging），**不 import services/blueprints**。
铁律：
  * **顶层不 import PyQt6** —— 无 Qt 的测试解释器也要能 ``import wwdm_app.gui``
    做纯逻辑/结构测试；Qt 只在 :func:`run`（及 qt_* 模块的构造点）里 import；
  * Flask 由 run.py 放**子线程**，主线程跑 ``QApplication.exec()``；
  * 业务动作一律走**回环 HTTP**（:mod:`gui.actions`），不直接调 services。

形态：悬浮球（qt_fab）是真实的无边框半透明圆球；托盘（qt_tray）是
``QSystemTrayIcon``。设置里关掉悬浮球（fab_visible=False）就只剩托盘。
"""
import os
import sys

from . import actions, autostart, pos  # noqa: F401  （纯函数/公共动作从这里再导出）


def should_run_gui(argv=None, env=None, has_qt=None):
    """本次启动是否该走 GUI（PyQt6）路径。

    判定顺序（任一为否则降级为纯 Flask 模式）：
      1. ``--no-gui`` 参数或 ``WWDMUI_NO_GUI=1`` 环境变量；
      2. import PyQt6 失败（未装 Qt / 精简环境）；
      3. 无显示环境（Windows 会话 0 服务里跑、SSH 无 DISPLAY）。
    参数可注入，便于测试。
    """
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if "--no-gui" in argv:
        return False
    if str(env.get("WWDMUI_NO_GUI", "")).strip() in ("1", "true", "yes"):
        return False
    if has_qt is None:
        if not _qt_available():
            return False
    elif not has_qt:
        return False
    return _has_display()


def _qt_available():
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401
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
        return bool(ctypes.windll.user32.GetShellWindow())
    except Exception:
        return os.environ.get("SESSIONNAME", "") not in ("", "Services")


def save_fab_pos(x, y):
    """保存悬浮球位置（同进程原地更新 + 落盘，不做 HTTP 绕路）。"""
    actions.save_flag("fab_pos", pos.fmt_pos(x, y))


def run(port, token="", autostart_flag=False, server_dead=None,
        fab_visible=True, smoke_ms=0):
    """GUI 总入口：建 QApplication →（可选）悬浮球 → 托盘 → ``app.exec()``。

    * ``fab_visible=False`` → 只显示托盘，入口不丢；
    * ``smoke_ms>0`` 冒烟口子：N 毫秒后 ``app.quit()``；
    * 球或托盘任一侧建失败不让整体陪葬：球挂了还有托盘，托盘挂了还有球。
    """
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from . import qt_fab, qt_theme, qt_tray

    app = QApplication.instance() or QApplication(sys.argv)
    # 关掉笔记窗/弹窗不应退出进程 —— 常驻托盘
    app.setQuitOnLastWindowClosed(False)
    qt_theme.apply(app)

    ball = qt_fab.create("127.0.0.1", port, token=token,
                         server_dead=server_dead, fab_visible=fab_visible)
    tray = qt_tray.create("127.0.0.1", port, token=token, fab=ball,
                          server_dead=server_dead)

    if not tray.is_available() and not ball.isVisible():
        # 托盘没挂上、球也没显示 → 没有任何入口
        try:
            tray.destroy()
        except Exception:
            pass
        raise RuntimeError("悬浮球与托盘都没建起来，GUI 无入口可用")

    if smoke_ms and int(smoke_ms) > 0:
        QTimer.singleShot(int(smoke_ms), app.quit)
    try:
        return app.exec()
    finally:
        try:
            tray.destroy()
        except Exception:
            pass
