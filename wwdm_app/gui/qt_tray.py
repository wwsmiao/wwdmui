# -*- coding: utf-8 -*-
"""PyQt6 系统托盘（v6.1）：右下角停靠图标 + 完整右键菜单。

v6.0 的托盘用 ctypes ``Shell_NotifyIconW`` + 自建消息窗，踩过 WndProc 里调
tkinter 导致 GIL 崩溃的坑（v6.0(4)）。v6.1 改用 Qt 原生 ``QSystemTrayIcon``——
没有自建窗口过程，从根上消除那类崩溃。

左键/右键都弹出菜单（全功能入口）；双击直接打开管理器网页。
业务动作走 :mod:`gui.actions`（回环 HTTP）。顶层不 import PyQt6。
"""
from . import actions, qt_fab, qt_theme


class _TrayImpl:
    """真实实现（需 PyQt6）；由 :func:`create` 惰性构造。"""

    def __init__(self, host, port, token="", fab=None, server_dead=None):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QCursor, QIcon
        from PyQt6.QtWidgets import QMenu, QSystemTrayIcon
        self._QSystemTrayIcon = QSystemTrayIcon
        self._QCursor = QCursor
        self.host = host
        self.port = int(port)
        self.token = token or ""
        self.fab = fab
        self.server_dead = server_dead

        self.icon = QSystemTrayIcon(QIcon(qt_theme.icon_path()))
        self.icon.setToolTip(self._tip())
        self._menu = QMenu()
        self.icon.activated.connect(self._on_activated)
        self.icon.show()

        self._timer = None
        if server_dead is not None:
            self._timer = QTimer()
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._refresh_tip)
            self._timer.start()

    # ---------------- tooltip ----------------
    def _tip(self):
        base = "wwdmui 模型管理器 %s:%d" % (self.host, self.port)
        if self.server_dead is not None and self.server_dead.is_set():
            base += "（服务已停止）"
        return base

    def _refresh_tip(self):
        try:
            self.icon.setToolTip(self._tip())
        except Exception:
            pass

    # ---------------- 菜单 ----------------
    def _state(self):
        visible = False
        if self.fab is not None:
            try:
                visible = bool(self.fab.isVisible())
            except Exception:
                visible = False
        return {
            "autostart": actions.autostart_enabled(),
            "visible": visible,
            "theme_label": actions.ui_theme_label(),
        }

    def _rebuild_menu(self):
        self._menu.clear()
        qt_fab.build_menu(self._menu, self._state(), self.host, self.port,
                          self._on_action)

    def _on_activated(self, reason):
        R = self._QSystemTrayIcon.ActivationReason
        if reason in (R.Trigger, R.Context):
            self._rebuild_menu()
            self._menu.exec(self._QCursor.pos())
        elif reason == R.DoubleClick:
            actions.open_ui(self.host, self.port, self.token)

    def _on_action(self, key):
        if key == "open":
            actions.open_ui(self.host, self.port, self.token)
        elif key == "copy":
            actions.copy_url(self.host, self.port, self.token)
        elif key == "autostart":
            actions.toggle_autostart()
        elif key == "shutdown":
            mins = qt_theme.ask_minutes(
                None, "定时关机", "多少分钟后关机？（0 = 立即，最多 1440）",
                1, 0, 1440)
            if mins is not None:
                actions.schedule_shutdown(self.host, self.port, int(mins) * 60)
        elif key == "theme":
            self._toggle_theme()
        elif key == "notes":
            from . import qt_notes
            qt_notes.open_notes(self.host, self.port)
        elif key == "douyin":
            from . import qt_douyin
            qt_douyin.open_douyin(self.host, self.port)
        elif key == "toggle":
            if self.fab is not None:
                try:
                    self.fab.set_visible(not self.fab.isVisible())
                    self._refresh_tip()
                except Exception:
                    pass
        elif key == "quit":
            actions.quit_app(self.host, self.port)

    def _toggle_theme(self):
        from ..core import config
        from PyQt6.QtWidgets import QApplication
        cur = str(config.settings.get("ui_theme", "light")).strip().lower()
        nxt = "dark" if cur != "dark" else "light"
        got = actions.set_ui_theme(self.host, self.port, nxt)
        if got:
            config.settings["ui_theme"] = got
            app = QApplication.instance()
            if app is not None:
                qt_theme.apply(app)

    # ---------------- 生命周期 ----------------
    def is_available(self):
        try:
            return bool(self._QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            return False

    def destroy(self):
        try:
            if self._timer is not None:
                self._timer.stop()
        except Exception:
            pass
        try:
            self.icon.hide()
        except Exception:
            pass


def create(host, port, token="", fab=None, server_dead=None):
    """创建并显示托盘图标；返回实现对象（调用方需持有引用防 GC）。"""
    return _TrayImpl(host, port, token=token, fab=fab, server_dead=server_dead)
