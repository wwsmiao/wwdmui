# -*- coding: utf-8 -*-
"""tkinter 悬浮球（v6.0）—— exe 启动后桌面右下角的「软件设置按钮」。

形态
----
无边框置顶小圆窗（Windows 用 ``-transparentcolor`` 挖掉方角；不支持该属性的
平台退化为圆角矩形，功能不变）：

  * **左键单击** → 打开管理器 Web 界面（延迟 1.2s 就绪轮询见 run.py）；
  * **左键拖动** → 移动位置，松手时钳制在屏幕内并写回 settings.json；
  * **右键** → 菜单：打开界面 / 复制访问地址 / 开机自启动切换 / 隐藏悬浮球 / 退出。

v6.0(2) 起与托盘（:mod:`gui.tray`）共生：球窗是**共享 root 的 Toplevel**，
托盘菜单可随时把球收起来/再拉起来（:meth:`FabBall.show` / :meth:`FabBall.hide`）。
菜单动作本体在 :mod:`gui.actions`，两边共用一份。

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

from . import actions, pos
from . import icoimage
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
        self.root = None                    # tkinter 根（可能是与托盘共享的）
        self._win = None                    # 球窗本体（Toplevel）
        self._canvas = None
        self._oval = None
        self._img_normal = None      # 悬浮球图标（wwdm_cat.ico），贴图模式
        self._img_hover = None
        self._img_item = None
        self._drag = None            # (x_root, y_root, moved) 拖动状态
        self._grab = (0, 0)
        self._standalone = False     # True=自建 root（单球模式/冒烟），销毁时连根拔
        self._after_id = None        # 探活定时器 id（销毁时 cancel）

    # ---------------- URL ----------------
    def ui_url(self, with_token=True):
        return actions.ui_url(self.host, self.port, self.token,
                              with_token=with_token)

    # ---------------- 启动 ----------------
    def build(self, root):
        """在给定 tkinter root 上建球窗（Toplevel）。幂等：已建则直接返回。

        与 :meth:`start` 的区别：不建自己的 root、不进 mainloop —— 供托盘
        共享 root（v6.0(2)）与「只托盘」模式下由菜单重新拉起球用。
        """
        if self._win is not None:
            return self._win
        import tkinter as tk
        self.root = root
        win = tk.Toplevel(root)
        self._win = win
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = pos.resolve_pos(_saved_pos(), (sw, sh))
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (FAB_SIZE, FAB_SIZE, x, y))
        try:
            # Windows 专属：magenta 区域全透明 → 圆形 hitbox
            win.wm_attributes("-transparentcolor", _COLORS["chroma"])
        except Exception:
            pass                        # macOS/Linux 不支持：退化为圆角矩形

        cv = tk.Canvas(win, width=FAB_SIZE, height=FAB_SIZE,
                       bg=_COLORS["chroma"], highlightthickness=0)
        self._canvas = cv
        # v6.0(5)：优先贴 wwdm_cat 图标（exe / 托盘 / 球统一）；ico 解不出
        #（非 PNG 条目 / 资源缺失）→ 回退旧的纯色圆 + 「设」字，任何环境都能用。
        self._img_item = None
        self._oval = None
        imgs = icoimage.ball_images(icoimage.default_icon_path(), FAB_SIZE,
                                    master=win)
        if imgs is not None:
            self._img_normal, self._img_hover = imgs
            self._img_item = cv.create_image(FAB_SIZE // 2, FAB_SIZE // 2,
                                             image=self._img_normal)
        else:
            self._img_normal = self._img_hover = None
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
        cv.bind("<Enter>", lambda e: self._paint(True))
        cv.bind("<Leave>", lambda e: self._paint(False))

        win.protocol("WM_DELETE_WINDOW", self._on_close)
        return win

    def start(self, dry_run=False, smoke_ms=0):
        """自建隐藏 root + 建球 + mainloop 阻塞（单球模式/冒烟用）。

        dry_run=True 只验参数不建窗；smoke_ms>0 建真窗但 N 毫秒后自毁
        （无头 CI / 打包后冒烟「窗能建、属性不炸、几何正确」用，生产不传）。
        与托盘共生的正常路径走 :func:`gui.run`（共享 root），不经过这里。
        """
        if dry_run:
            return True
        import tkinter as tk
        enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()                      # 根常驻隐藏，球是它的 Toplevel
        self._standalone = True
        self.build(root)
        if smoke_ms and int(smoke_ms) > 0:
            # 冒烟口子（见 start docstring）：到点自毁，不用人肉关
            def _bye():
                self._cancel_poll()
                root.destroy()
            root.after(int(smoke_ms), _bye)
        if self.server_dead is not None:
            self._poll_server()
        root.mainloop()
        return True

    def is_visible(self):
        """球窗是否在桌上（托盘菜单据此切「显示/隐藏」文案）。"""
        if self._win is None:
            return False
        try:
            return bool(self._win.winfo_exists())
        except Exception:
            return False

    def show(self):
        """显示悬浮球（托盘「显示悬浮球」用）；被隐藏过则重建并记住选择。"""
        if self.is_visible():
            return True
        if self.root is None:
            return False
        try:
            self.build(self.root)
            _save_flag("fab_visible", True)
            return True
        except Exception:
            return False

    def _poll_server(self):
        """每 500ms 看 Flask 子线程是否还活着；死了就收球并弹提示。

        GUI 模式下控制台不存在 —— 服务起不来（端口占用等）时球是唯一的
        反馈渠道，不能留一个打不开界面的球在桌上。``server_dead=None``
        （冒烟/单测）时不轮询。
        """
        if self.server_dead is None:
            return
        if self.server_dead.is_set():
            self._die_no_server()
            return
        try:
            if self.root is not None:
                self._after_id = self.root.after(500, self._poll_server)
        except Exception:
            pass                        # root 已销毁（冒烟自毁/退出）：安静退出轮询

    def _cancel_poll(self):
        """掐掉挂起的探活定时器（root 销毁后它还撞 Tcl 会刷错误日志）。"""
        aid, self._after_id = self._after_id, None
        if aid and self.root is not None:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass

    def _die_no_server(self):
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "管理器没能启动",
                "Web 服务没有起来（端口可能被占用）。\n"
                "请关掉占用该端口的程序，或在「设置 → 管理器访问」里换个端口。")
        except Exception:
            pass
        self.hide(save=False)

    # ---------------- 绘制 ----------------
    def _paint(self, hover):
        if self._canvas is None:
            return
        if self._img_item is not None:
            self._canvas.itemconfig(
                self._img_item,
                image=self._img_hover if hover else self._img_normal)
        elif self._oval is not None:
            self._canvas.itemconfig(
                self._oval, fill=_COLORS["bg_hover"] if hover else _COLORS["bg"])

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
        sw, sh = self._win.winfo_screenwidth(), self._win.winfo_screenheight()
        nx, ny = pos.clamp_pos(nx, ny, (sw, sh))
        self._win.geometry("+%d+%d" % (nx, ny))

    def _on_release(self, e):
        moved = bool(self._drag and self._drag[2])
        self._drag = None
        if moved:
            self._persist_pos()
            return
        self._open_ui()

    def _on_close(self):
        # 点右下角「关闭」只有一种来路：菜单里没有关闭，这里是系统兜底
        self.hide(save=False)

    # ---------------- 动作（本体在 gui/actions，托盘共用） ----------------
    def _open_ui(self):
        actions.open_ui(self.host, self.port, self.token)

    def _copy_url(self):
        actions.copy_url(self.root, self.host, self.port, self.token)

    def _persist_pos(self):
        g = self._win.geometry()            # "56x56+x+y"
        try:
            xy = g.split("+", 1)[1]
            sx, sy = xy.split("+")
            from . import save_fab_pos
            save_fab_pos(int(sx), int(sy))
        except Exception:
            pass

    def _toggle_autostart(self):
        want = actions.toggle_autostart()
        if want is not None:
            self.autostart_flag = want

    def _hide(self, save=True):
        # 🔴 v6.0(5)：公开为 ``hide``（托盘 _toggle_fab 调用；此前叫 _hide 缺失
        # 公开名 → AttributeError 被 except 吞掉 → 菜单「显示悬浮球」无功能）。
        self.hide(save=save)

    def hide(self, save=True):
        if save:
            _save_flag("fab_visible", False)
        win, self._win = self._win, None
        self._canvas = None
        self._oval = None
        self._img_normal = self._img_hover = self._img_item = None
        try:
            if win is not None:
                win.destroy()               # 只销球窗；共享 root 要留给托盘
        except Exception:
            pass
        if self._standalone:
            # 单球模式（无托盘）：球没了 root 也没有存在意义
            try:
                if self.root is not None:
                    self._cancel_poll()
                    self.root.destroy()
            except Exception:
                pass

    def _quit(self):
        # 铁律：不直接调 services.cleanup_all（global 重绑定非线程安全），
        # 走回环 HTTP 让 Flask 那侧自己清理后 os._exit。
        actions.quit_app(self.host, self.port)

    # ---------------- 右键菜单 ----------------
    def _on_menu(self, e):
        import tkinter as tk
        m = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei", 10))
        m.add_command(label="打开管理器界面", command=self._open_ui)
        m.add_command(label="复制访问地址", command=self._copy_url)
        m.add_command(label=actions.autostart_label(actions.autostart_enabled()),
                      command=self._toggle_autostart)
        m.add_separator()
        m.add_command(label="隐藏悬浮球", command=lambda: self.hide(save=True))
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
    actions.save_flag(key, value)


def start(port, token="", autostart_flag=False, dry_run=False, server_dead=None,
          smoke_ms=0):
    """便捷入口：建球并阻塞主线程。返回 True 表示正常走完（dry_run 恒 True）。"""
    return FabBall(port, token=token, autostart_flag=autostart_flag,
                   server_dead=server_dead).start(dry_run=dry_run, smoke_ms=smoke_ms)
