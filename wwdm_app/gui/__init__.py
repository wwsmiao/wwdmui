# -*- coding: utf-8 -*-
"""v6.0 桌面入口层：悬浮球 + 托盘图标（v6.0(2) 起两者共生）。

依赖方向：``gui → core``（config/logging），**不 import services/blueprints** ——
保持 core 铁律「基础层不许被 GUI 反向依赖业务」的镜像版：GUI 层也不许依赖业务层。

铁律（fab.py / tray.py 同）：
  * tkinter 只在**主线程**创建/操作，Flask 由 run.py 放子线程；
  * GUI **不直接调 services**（``comfyui_proc`` 等有 global 重绑定，非线程安全），
    一切业务动作走回环 HTTP 打自己的 API；
  * 一切从消息循环回到 UI 的动作都经 ``root.after(0, fn)`` —— **ctypes
    WndProc 例外**：那里一个 tkinter API 都不许碰，只许纯 Python 入队
    （``tray._enqueue`` + 25ms 消费定时器，原因见 tray.py 模块 docstring
    的 GIL 崩溃机理）。

形态（:func:`run` 协调）：一个常驻隐藏的共享 root 负责消息泵 —— 悬浮球是它的
Toplevel（可收起来），托盘是 ctypes 消息窗（:mod:`gui.tray`）。设置里关掉
悬浮球（``fab_visible=False``）就只剩托盘，入口不丢。
"""
import os
import sys

from . import actions, autostart, pos  # noqa: F401  （纯函数/公共动作从这里再导出）
from . import fab  # noqa: F401  （fab 顶层不碰 tkinter，只在 start()/build() 内 import）
from . import tray  # noqa: F401  （tray 顶层同样不碰 tkinter，ctypes 句柄在方法里建）


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
    actions.save_flag("fab_pos", pos.fmt_pos(x, y))


def run(port, token="", autostart_flag=False, server_dead=None,
        fab_visible=True, smoke_ms=0):
    """GUI 总入口：建共享 root →（可选）悬浮球 → 托盘 → mainloop 阻塞主线程。

    * ``fab_visible=False``（设置里关掉了悬浮球）→ 只有托盘，入口不丢；
    * ``smoke_ms>0`` 冒烟口子：N 毫秒后连托盘带 root 一起自毁；
    * 球或托盘任一侧建失败不让整体陪葬：球挂了还有托盘，托盘挂了还有球
      （非 Windows 上 attach 直接返回 False，run.py 本就只在 Windows GUI
      分支调到这里）。
    """
    import tkinter as tk
    from . import fab as _fab
    from . import tray as _tray
    from . import theme as _theme
    _fab.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()                      # 共享 root 常驻隐藏，专职消息泵
    _theme.apply_style(root)             # 给整棵 gui 树挂网页水彩 ttk 样式（幂等）

    # 🔴 v6.0(5)：无论 fab_visible 都建 FabBall 实例并**先绑共享 root** ——
    # 「只托盘」模式下托盘菜单「显示悬浮球」靠它在现场建窗；此前 ball=None
    # 导致 _toggle_fab 直接 return，菜单点了没反应（悬浮球「无法显示」）。
    ball = _fab.FabBall(port, token=token, autostart_flag=autostart_flag,
                        server_dead=server_dead)
    ball.root = root                          # 绑定共享 root（菜单可随时拉起球）
    if fab_visible:
        try:
            ball.build(root)
        except Exception:
            pass                              # 建不起来还有托盘，别整体降级

    icon = _tray.TrayIcon(port, token=token, fab=ball, server_dead=server_dead)
    attached = icon.attach(root)
    if not attached and not ball.is_visible():
        # 托盘没挂上、球也没建起来 → 没有任何入口，整体无意义
        try:
            icon.destroy()
        except Exception:
            pass
        root.destroy()
        raise RuntimeError("悬浮球与托盘都没建起来，GUI 无入口可用")

    if smoke_ms and int(smoke_ms) > 0:
        def _bye():
            icon.destroy()
            try:
                root.destroy()
            except Exception:
                pass
        root.after(int(smoke_ms), _bye)
    root.mainloop()
