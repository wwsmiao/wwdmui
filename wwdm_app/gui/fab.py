# -*- coding: utf-8 -*-
"""tkinter 悬浮球（v6.0）—— exe 启动后桌面右下角的「软件设置按钮」。

形态
----
无边框置顶小圆窗（Windows 用 ``-transparentcolor`` 挖掉方角；不支持该属性的
平台退化为圆角矩形，功能不变）：

  * **左键单击** → 打开管理器 Web 界面（延迟 1.2s 就绪轮询见 run.py）；
  * **左键拖动** → 移动位置，松手时钳制在屏幕内并写回 settings.json；
  * **右键** → 菜单：打开界面 / 复制访问地址 / 开机自启动切换 / 隐藏悬浮球 / 退出。

铁律（改动前必读）
----------------
1. **tkinter 只在主线程创建/操作**。本模块不自己起线程；run.py 把 Flask 放
   子线程、把主线程留给 ``root.mainloop()``。
2. **不直接调 services**（``comfyui_proc`` / ``aria2_proc`` 等有 global 重绑定，
   非线程安全）。「退出管理器」这种带清理的动作走**回环 HTTP 打自己的 API**。
3. 所有从别处回到 UI 的更新都必须包 ``root.after(0, fn)``。
4. 回环请求必须禁代理（本机 7890 等代理会转发 localhost，见 MEMORY-detail）。

无头/降级
--------
``start(dry_run=True)`` 不建真窗，只做 import 与参数校验 —— 供 CI / 无显示
环境冒烟。正常路径见 :func:`gui.should_run_gui`。
"""
import os
import sys
import time
import webbrowser

from . import autostart, pos
from .pos import FAB_SIZE

_COLORS = {
    "bg":      "#4a6fa5",     # 水彩 cyan（基座色，不随 Web 预设变）
    "bg_hover": "#3d5d8a",
    "fg":      "#ffffff",
    "chroma":  "magenta",     # 透明挖孔色（只出现在被挖掉的区域）
}

_DRAG_THRESHOLD = 5          # 位移小于该值算单击（px）


def enable_dpi_awareness():
    """Per-Monitor DPI 感知，必须在建任何窗口**之前**调用。

    不调的话高 DPI 屏（125%/150% 缩放）上 tkinter 窗口会被系统拉伸糊掉。
    失败（老 Windows 没有 shcore）静默忽略 —— 糊总比没有好。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor
        return True
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception:
            return False


class FabBall:
    """悬浮球。构造只存参数；``start()`` 里才碰 tkinter。"""

    def __init__(self, port, host="127.0.0.1", token="", autostart_flag=False,
                 server_dead=None):
        self.port = int(port)
        self.host = host or "127.0.0.1"
        self.token = token or ""
        self.autostart_flag = bool(autostart_flag)
        self.server_dead = server_dead      # threading.Event：Flask 子线程死了置位
        self.root = None
        self._canvas = None
        self._oval = None
        self._drag = None            # (x_root, y_root, moved) 拖动状态
        self._grab = (0, 0)

    # ---------------- URL ----------------
    def ui_url(self, with_token=True):
        base = "http://%s:%d" % (self.host, self.port)
        if with_token and self.token:
            base += "/?t=" + self.token
        return base

    # ---------------- 启动 ----------------
    def start(self, dry_run=False, smoke_ms=0):
        """建窗并进入消息循环（阻塞，占用调用线程 —— 必须是主线程）。

        dry_run=True 只验参数不建窗；smoke_ms>0 建真窗但 N 毫秒后自毁
        （无头 CI / 打包后冒烟「窗能建、属性不炸、几何正确」用，生产不传）。
        """
        if dry_run:
            return True
        import tkinter as tk
        enable_dpi_awareness()
        root = tk.Tk()
        self.root = root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = pos.resolve_pos(_saved_pos(), (sw, sh))
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry("%dx%d+%d+%d" % (FAB_SIZE, FAB_SIZE, x, y))
        try:
            # Windows 专属：magenta 区域全透明 → 圆形 hitbox
            root.wm_attributes("-transparentcolor", _COLORS["chroma"])
        except Exception:
            pass                        # macOS/Linux 不支持：退化为圆角矩形

        cv = tk.Canvas(root, width=FAB_SIZE, height=FAB_SIZE,
                       bg=_COLORS["chroma"], highlightthickness=0)
        self._canvas = cv
        self._oval = cv.create_oval(2, 2, FAB_SIZE - 2, FAB_SIZE - 2,
                                    fill=_COLORS["bg"], outline="")
        cv.create_text(FAB_SIZE // 2, FAB_SIZE // 2, text="设",
                       fill=_COLORS["fg"],
                       font=("Microsoft YaHei", 18, "bold"))
        cv.pack(fill="both", expand=True)

        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_motion)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Button-3>", self._on_menu)
        cv.bind("<Enter>", lambda e: self._paint(_COLORS["bg_hover"]))
        cv.bind("<Leave>", lambda e: self._paint(_COLORS["bg"]))

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if smoke_ms and int(smoke_ms) > 0:
            # 冒烟口子（见 start docstring）：到点自毁，不用人肉关
            root.after(int(smoke_ms), root.destroy)
        if self.server_dead is not None:
            self._poll_server()
        root.mainloop()
        return True

    def _poll_server(self):
        """每 500ms 看 Flask 子线程是否还活着；死了就收球并弹提示。

        GUI 模式下控制台不存在 —— 服务起不来（端口占用等）时球是唯一的
        反馈渠道，不能留一个打不开界面的球在桌上。
        """
        if self.server_dead is not None and self.server_dead.is_set():
            self._die_no_server()
            return
        if self.root is not None:
            self.root.after(500, self._poll_server)

    def _die_no_server(self):
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "管理器没能启动",
                "Web 服务没有起来（端口可能被占用）。\n"
                "请关掉占用该端口的程序，或在「设置 → 管理器访问」里换个端口。")
        except Exception:
            pass
        self._hide(save=False)

    # ---------------- 绘制 ----------------
    def _paint(self, color):
        if self._canvas is not None and getattr(self, "_oval", None):
            self._canvas.itemconfig(self._oval, fill=color)

    # ---------------- 鼠标 ----------------
    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, False)
        self._grab = (e.x, e.y)

    def _on_motion(self, e):
        if not self._drag:
            return
        ox, oy, _ = self._drag
        dx, dy = e.x_root - ox, e.y_root - oy
        if abs(dx) > _DRAG_THRESHOLD or abs(dy) > _DRAG_THRESHOLD:
            self._drag = (ox, oy, True)
        if not self._drag[2]:
            return
        gx, gy = getattr(self, "_grab", (0, 0))
        nx, ny = e.x_root - gx, e.y_root - gy
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        nx, ny = pos.clamp_pos(nx, ny, (sw, sh))
        self.root.geometry("+%d+%d" % (nx, ny))

    def _on_release(self, e):
        moved = bool(self._drag and self._drag[2])
        self._drag = None
        if moved:
            self._persist_pos()
            return
        self._open_ui()

    def _on_close(self):
        # 点右下角「关闭」只有一种来路：菜单里没有关闭，这里是系统兜底
        self._hide(save=False)

    # ---------------- 动作 ----------------
    def _open_ui(self):
        try:
            webbrowser.open(self.ui_url())
        except Exception:
            pass

    def _copy_url(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.ui_url())
        except Exception:
            pass

    def _persist_pos(self):
        g = self.root.geometry()             # "56x56+x+y"
        try:
            xy = g.split("+", 1)[1]
            sx, sy = xy.split("+")
            from . import save_fab_pos
            save_fab_pos(int(sx), int(sy))
        except Exception:
            pass

    def _toggle_autostart(self):
        want = not self.autostart_flag
        ok = autostart.sync(want)
        if ok:
            self.autostart_flag = want
            _save_flag("autostart", want)

    def _hide(self, save=True):
        if save:
            _save_flag("fab_visible", False)
        try:
            self.root.destroy()
        except Exception:
            pass

    def _quit(self):
        # 铁律：不直接调 services.cleanup_all（global 重绑定非线程安全），
        # 走回环 HTTP 让 Flask 那侧自己清理后 os._exit。
        self._http_post("/api/system/quit")

    def _http_post(self, path):
        import json
        import urllib.request
        url = "http://%s:%d%s" % (self.host, self.port, path)
        data = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(req, timeout=3).read()
        except Exception:
            # 回环失败（服务没起来/端口错）也别让球卡在桌上：直接退
            os._exit(0)

    # ---------------- 右键菜单 ----------------
    def _on_menu(self, e):
        import tkinter as tk
        m = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei", 10))
        m.add_command(label="打开管理器界面", command=self._open_ui)
        m.add_command(label="复制访问地址", command=self._copy_url)
        m.add_command(label=("关闭开机自启动" if self.autostart_flag else "开机自动启动"),
                      command=self._toggle_autostart)
        m.add_separator()
        m.add_command(label="隐藏悬浮球", command=lambda: self._hide(save=True))
        m.add_command(label="退出管理器", command=self._quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()


# ---------------- 设置读写辅助（同进程，原地改一个键） ----------------
def _saved_pos():
    from ..core import config
    return config.settings.get("fab_pos", "")


def _save_flag(key, value):
    from ..core import config
    config.settings[key] = value
    try:
        config.save_settings(config.settings)
    except Exception:
        pass


def start(port, token="", autostart_flag=False, dry_run=False, server_dead=None,
          smoke_ms=0):
    """便捷入口：建球并阻塞主线程。返回 True 表示正常走完（dry_run 恒 True）。"""
    return FabBall(port, token=token, autostart_flag=autostart_flag,
                   server_dead=server_dead).start(dry_run=dry_run, smoke_ms=smoke_ms)
