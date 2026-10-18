# -*- coding: utf-8 -*-
"""Windows 托盘图标（v6.0(2)）—— 悬浮球被隐藏后的找回入口，「只托盘」模式的全部入口。

实现要点
--------
零第三方依赖：``Shell_NotifyIconW`` + 自建**消息窗口**（``HWND_MESSAGE`` 父窗，
只收消息不可见），窗口过程用 ctypes ``WINFUNCTYPE`` 挂上；托盘回调消息
（``WM_APP+1``）与菜单命令（``WM_COMMAND``）都回到本线程。

与 tkinter 的关系：消息窗口建在 **tkinter 主线程**上，消息泵由共享 root 的
``mainloop()`` 统一提供（同线程所有窗口共享消息队列）。

🔴 v6.0(4) 铁律：**WndProc 里一个 tkinter API 都不许碰**（含 ``root.after``）。
WndProc 是 ``Tcl_DoOneEvent`` 内经 ``DispatchMessageW`` 调起来的（GIL 释放区
+ ctypes TEMP 线程状态），此时 ``Tkapp_Call``（``root.after`` 内部就走它）
会把线程局部 ``tcl_tstate`` 覆写成 TEMP 再置 NULL；同一次 ``Tcl_DoOneEvent``
里随后派发的定时器/命令事件走 ``ENTER_PYTHON`` → ``PyEval_RestoreThread(NULL)``
→ 进程级 Fatal Error（**点一次托盘图标崩一次**，已写复现脚本实证）。
WndProc 只许纯 Python 入队（:meth:`TrayIcon._enqueue`），动作由 25ms 消费
定时器在主循环上下文里执行（:meth:`TrayIcon._drain_pending`）。

菜单（tkinter Menu，与悬浮球右键菜单同款式）：
  打开管理器界面 / 复制访问地址 / 开机自启动切换 / 显示·隐藏悬浮球 / 退出。
  ⚠️ 刻意**没有「重启管理器」**：Werkzeug 开了 SO_REUSEADDR，两个进程能同时
  LISTEN 同一端口、先绑定者吃流量 —— 只放「退出」（同 v6.0(1) 约定）。

降级
----
``start(dry_run=True)`` 不建真托盘只验参数；``smoke_ms>0`` 建真托盘、N 毫秒
后自毁（打包后冒烟用，生产不传）。非 Windows / 任何 Win32 失败：
:meth:`TrayIcon.attach` 返回 False，调用方降级为「只有悬浮球」。
"""
import ctypes
import os
from ctypes import wintypes

from . import actions
from . import fab as _fab        # 只用 enable_dpi_awareness；fab 顶层不碰 tkinter

# ---------------- Win32 常量 ----------------
WM_APP = 0x8000
WM_TRAY = WM_APP + 1             # 自定义托盘回调消息
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

HWND_MESSAGE = -3                # 消息窗口父窗（只收消息，不可见）
IDI_APPLICATION = 32511          # 系统自带应用图标（共享，不 Destroy）
IMAGE_ICON = 2
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

# 菜单命令 id（WM_COMMAND 的 wParam 低 16 位）
CMD_OPEN = 1001
CMD_COPY = 1002
CMD_AUTOSTART = 1003
CMD_TOGGLE_FAB = 1004
CMD_QUIT = 1005

_TIP_MAX = 127                   # szTip 是 128 WCHAR，留一个给 NUL
_DRAIN_MS = 25                   # WndProc 待办队列消费周期（见 _enqueue 注释）

_ASSET_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "wwdm_cat.ico")

_bound = False                   # argtypes 只绑一次（幂等，防重复设置）


def format_tip(text):
    """钳到 NOTIFYICONDATAW.szTip 容量（超长静默截断，不炸）。"""
    return str(text)[:_TIP_MAX]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 的入参。字段顺序照 MSDN（64 位自然对齐）。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateFlags", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),            # 文档名 uVersionOrTimeout
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),       # GUID
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),       # 绑定后再赋值（见 _bind）
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _bind():
    """按真实签名绑定 Win32 函数（x64 下指针参数必须 8 字节，裸 int 会错位）。

    只绑一次；重复设置 argtypes 无害但没必要。非 Windows 不绑（调用方先判
    ``os.name``）。
    """
    global _bound
    if _bound:
        return
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    sh32 = ctypes.windll.shell32
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    u32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    u32.RegisterClassExW.restype = wintypes.ATOM
    u32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u32.CreateWindowExW.restype = wintypes.HWND
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
    u32.DefWindowProcW.restype = ctypes.c_ssize_t
    u32.DestroyWindow.argtypes = [wintypes.HWND]
    u32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                               wintypes.UINT, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]
    u32.LoadImageW.restype = wintypes.HANDLE
    u32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    u32.LoadIconW.restype = wintypes.HICON
    u32.DestroyIcon.argtypes = [wintypes.HICON]
    sh32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                       ctypes.POINTER(NOTIFYICONDATAW)]
    sh32.Shell_NotifyIconW.restype = wintypes.BOOL
    _bound = True


class TrayIcon:
    """托盘图标。构造只存参数；``attach()`` 才建消息窗 + 加图标。"""

    def __init__(self, port, host="127.0.0.1", token="", fab=None,
                 server_dead=None):
        self.port = int(port)
        self.host = host or "127.0.0.1"
        self.token = token or ""
        self.fab = fab                    # FabBall 实例或 None（只托盘模式）
        self.server_dead = server_dead    # threading.Event：Flask 子线程死了置位
        self._root = None                 # tkinter 根（attach 时给定）
        self._hwnd = None
        self._hicon = None
        self._icon_owned = False          # LoadImageW 加载的才需 DestroyIcon
        self._proc = None                 # WNDPROC 回调：必须防 GC
        self._class_name = ""
        self._hinst = None
        self._dead_notified = False
        self._after_id = None             # 探活定时器 id（销毁时 cancel）
        self._drain_id = None             # 待办消费定时器 id（销毁时 cancel）
        self._draining = False            # 消费重入卫兵（模态循环里防重入）
        self._pending = []                # WndProc → 主循环的安全中转（纯 Python）

    # ---------------- 安装 / 卸载 ----------------
    def attach(self, root):
        """在既有 tkinter root 上建消息窗 + 加托盘图标。返回是否成功。

        非 Windows / 任何 Win32 失败 → False（调用方降级为只有悬浮球，
        不该让托盘把整个 GUI 拖死）。
        """
        if os.name != "nt":
            return False
        try:
            _bind()
            self._root = root
            self._register_class()
            self._create_window()
            self._add_icon()
            self._poll_server()
            self._arm_drain()
            return True
        except Exception:
            self.destroy()
            return False

    def _register_class(self):
        _bind()
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        # 类名带实例 id：同进程多实例（测试/冒烟）不撞车
        name = "wwdmui_tray_msg_%d" % id(self)
        self._proc = WNDPROC(self._wndproc)     # 持引用，防被 GC
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = name
        atom = ctypes.windll.user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            raise ctypes.WinError(ctypes.get_last_error())
        self._class_name = name
        self._hinst = hinst

    def _create_window(self):
        hwnd = ctypes.windll.user32.CreateWindowExW(
            0, self._class_name, "wwdmui-tray", 0,
            0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
            None, wintypes.HINSTANCE(self._hinst), None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

    def _load_hicon(self):
        """图标加载：自带 assets/wwdm_cat.ico（exe/托盘/球统一）→ 回退系统应用图标。"""
        u32 = ctypes.windll.user32
        if os.path.isfile(_ASSET_ICON):
            h = u32.LoadImageW(None, _ASSET_ICON, IMAGE_ICON, 0, 0,
                               LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                self._icon_owned = True
                return h
        # 系统自带图标是共享资源：不能 DestroyIcon
        return u32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))

    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        self._hicon = self._load_hicon() or 0
        nid.hIcon = self._hicon
        nid.szTip = format_tip(self._tip_text())
        if not ctypes.windll.shell32.Shell_NotifyIconW(
                NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError(ctypes.get_last_error())

    def destroy(self):
        """删图标 + 销窗口 + 注销类。幂等，可反复调。"""
        if os.name != "nt":
            return
        self._cancel_poll()
        self._cancel_drain()
        try:
            if self._hwnd:
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = self._hwnd
                nid.uID = 1
                ctypes.windll.shell32.Shell_NotifyIconW(
                    NIM_DELETE, ctypes.byref(nid))
                ctypes.windll.user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        self._hwnd = None
        try:
            if self._class_name and self._hinst:
                ctypes.windll.user32.UnregisterClassW(self._class_name,
                                                      self._hinst)
        except Exception:
            pass
        self._class_name = ""
        if self._icon_owned and self._hicon:
            try:
                ctypes.windll.user32.DestroyIcon(self._hicon)
            except Exception:
                pass
        self._hicon = None
        self._icon_owned = False
        # 窗口已销毁，不会再进 WndProc —— 这时才放回调引用
        self._proc = None

    # ---------------- 窗口过程 ----------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        """消息窗口的窗口过程（在主线程消息泵里被调用）。"""
        try:
            if msg == WM_TRAY:
                # 托盘回调：wParam=uID，lParam 低 16 位是鼠标消息
                mouse = (lparam or 0) & 0xFFFF
                if mouse in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._enqueue(self._open_ui)
                elif mouse == WM_RBUTTONUP:
                    self._enqueue(self._post_menu)
            elif msg == WM_COMMAND:
                self._enqueue(lambda: self._dispatch((wparam or 0) & 0xFFFF))
        except Exception:
            pass
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _enqueue(self, fn):
        """WndProc → 主循环的唯一通道：只入队，绝不碰 tkinter。

        🔴 为什么不能像 fab 那样 ``root.after(0, fn)``：WndProc 是
        ``Tcl_DoOneEvent`` 内经 ``DispatchMessageW`` 调起来的（GIL 释放区、
        ctypes TEMP 线程状态），``Tkapp_Call`` 会把线程局部 ``tcl_tstate``
        覆写成 TEMP 再置 NULL；同一次 ``Tcl_DoOneEvent`` 里随后派发的任何
        定时器/命令事件走 ``ENTER_PYTHON`` → ``PyEval_RestoreThread(NULL)``
        → 进程级 Fatal Error（v6.0(4)：点一次托盘图标崩一次，已实证）。
        入队是纯 Python 操作，任何线程状态下都安全；消费见 _drain_pending。
        """
        if self._root is None:
            return
        self._pending.append(fn)

    def _drain_pending(self):
        """把 _enqueue 攒下的动作拿到主循环上下文里执行（tcl_tstate 有效）。

        单个动作抛异常不许阻断后面的（菜单动作互不相干）；重入卫兵防模态
        循环（messagebox 等）里定时器又打进来导致双份消费/双定时器。
        """
        if self._draining:
            return
        self._draining = True
        try:
            while self._pending:
                fn = self._pending.pop(0)
                try:
                    fn()
                except Exception:
                    pass
        finally:
            self._draining = False
        self._arm_drain()

    def _arm_drain(self):
        """挂/续 25ms 消费定时器（常驻；没有待办时只是一次空转）。"""
        if self._root is None:
            return
        try:
            self._drain_id = self._root.after(_DRAIN_MS, self._drain_pending)
        except Exception:
            pass

    def _cancel_drain(self):
        """掐掉挂起的消费定时器（root 销毁后它再撞 Tcl 会刷错误日志）。"""
        aid, self._drain_id = self._drain_id, None
        if aid and self._root is not None:
            try:
                self._root.after_cancel(aid)
            except Exception:
                pass

    # ---------------- 命令分发 ----------------
    def _dispatch(self, cmd):
        """菜单命令 → 动作（单测直接打这个，不建真托盘）。"""
        if cmd == CMD_OPEN:
            self._open_ui()
        elif cmd == CMD_COPY:
            self._copy_url()
        elif cmd == CMD_AUTOSTART:
            self._toggle_autostart()
        elif cmd == CMD_TOGGLE_FAB:
            self._toggle_fab()
        elif cmd == CMD_QUIT:
            self._quit()

    def _open_ui(self):
        actions.open_ui(self.host, self.port, self.token)

    def _copy_url(self):
        actions.copy_url(self._root, self.host, self.port, self.token)

    def _toggle_autostart(self):
        want = actions.toggle_autostart()
        if want is not None and self.fab is not None:
            # 同步悬浮球侧缓存，右键菜单文案才跟着翻转
            self.fab.autostart_flag = want

    def _toggle_fab(self):
        if self.fab is None:
            return
        try:
            if self.fab.is_visible():
                self.fab.hide(save=True)
            else:
                self.fab.show()
        except Exception:
            pass

    def _quit(self):
        actions.quit_app(self.host, self.port)

    # ---------------- 右键菜单 ----------------
    def _fab_label(self):
        if self.fab is not None:
            try:
                if self.fab.is_visible():
                    return "隐藏悬浮球"
            except Exception:
                pass
        return "显示悬浮球"

    def _post_menu(self):
        import tkinter as tk
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        m = tk.Menu(self._root, tearoff=0, font=("Microsoft YaHei", 10))
        m.add_command(label="打开管理器界面", command=self._open_ui)
        m.add_command(label="复制访问地址", command=self._copy_url)
        m.add_command(label=actions.autostart_label(actions.autostart_enabled()),
                      command=self._toggle_autostart)
        m.add_separator()
        m.add_command(label=self._fab_label(), command=self._toggle_fab)
        m.add_command(label="退出管理器", command=self._quit)
        try:
            m.tk_popup(pt.x, pt.y)
        finally:
            m.grab_release()

    def _autostart_on(self):
        return actions.autostart_enabled()

    # ---------------- 服务探活 ----------------
    def _cancel_poll(self):
        """掐掉挂起的探活定时器（root 销毁后它还撞 Tcl 会刷错误日志）。"""
        aid, self._after_id = self._after_id, None
        if aid and self._root is not None:
            try:
                self._root.after_cancel(aid)
            except Exception:
                pass

    def _tip_text(self):
        base = "wwdmui 模型管理器 %s:%d" % (self.host, self.port)
        if self.server_dead is not None and self.server_dead.is_set():
            base += "（服务已停止）"
        return base

    def _poll_server(self):
        """每 500ms 看 Flask 子线程是否还活着；死了改 tooltip 提示一次。

        悬浮球可见时由球弹 messagebox（球是主反馈渠道）；球被隐藏/没建时，
        tooltip 是唯一反馈，不能一声不吭。``server_dead=None``（冒烟/单测）
        时根本不轮询 —— 省得 root 销毁后定时器还往 Tcl 里撞。
        """
        if self.server_dead is None:
            return
        if self.server_dead.is_set():
            if not self._dead_notified:
                self._dead_notified = True
                try:
                    self.update_tip(format_tip(self._tip_text()))
                except Exception:
                    pass
            return
        try:
            if self._root is not None:
                self._after_id = self._root.after(500, self._poll_server)
        except Exception:
            pass

    def update_tip(self, text):
        """改 tooltip（NIM_MODIFY + NIF_TIP）。"""
        if not self._hwnd:
            return False
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_TIP
        nid.szTip = format_tip(text)
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(
            NIM_MODIFY, ctypes.byref(nid)))

    # ---------------- 启动 ----------------
    def start(self, dry_run=False, smoke_ms=0):
        """自建隐藏 root + 挂托盘 + mainloop 阻塞（只托盘模式/冒烟用）。

        dry_run=True 只验参数不建托盘；smoke_ms>0 建真托盘、N 毫秒后自毁。
        与悬浮球共存时走 :func:`gui.run`（共享 root），不经过这里。
        """
        if dry_run:
            return True
        import tkinter as tk
        _fab.enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        if not self.attach(root):
            root.destroy()
            return False
        if smoke_ms and int(smoke_ms) > 0:
            def _bye():
                self.destroy()
                try:
                    root.destroy()
                except Exception:
                    pass
            root.after(int(smoke_ms), _bye)
        root.mainloop()
        return True


def start(port, token="", fab=None, dry_run=False, server_dead=None,
          smoke_ms=0):
    """便捷入口：只托盘模式（无悬浮球）或独立冒烟。"""
    return TrayIcon(port, token=token, fab=fab,
                    server_dead=server_dead).start(dry_run=dry_run,
                                                   smoke_ms=smoke_ms)
