# -*- coding: utf-8 -*-
"""PyQt6 悬浮球（v6.1）：桌面右下角无边框半透明圆球。

交互
----
  * **左键单击** → 打开管理器网页；
  * **左键拖动** → 移动位置，松手钳制在屏幕内并写回 settings.json；
  * **右键** → 菜单（打开页面 / 复制地址 / 开机自启 / 定时关机 / 日夜间切换 /
    笔记本 / 隐藏悬浮球 / 退出）。

视觉（v6.1 重做）
----------------
自绘：柔和投影 + 猫图标 + hover 光晕，圆润通透；配色取自 :mod:`gui.qt_theme`
（网页水彩），随网页明暗切换。不是原生方按钮，是真正的「悬浮球」。

铁律：**顶层不 import PyQt6** —— QWidget 子类在 :func:`_fab_class` 内才定义
（无 Qt 的测试解释器也能 import 本模块测纯逻辑）。业务一律走回环 HTTP。
"""
from . import actions, pos, qt_theme

_BALL = 72       # 窗口边长（含投影留白）
_ICON = 52       # 猫图标绘制边长
_MARGIN = 10     # 投影留白


def menu_entries(state):
    """悬浮球右键菜单的纯描述：``[(label, key), ...]``；空 label 表示分隔符。

    key 供 :meth:`FabBall._on_action` 派发。纯函数，便于无 PyQt6 测试断言。
    按用户要求**不含任务中心**；「隐藏/显示悬浮球」按当前可见性动态。
    """
    theme_label = state.get("theme_label") or "切换到夜间"
    vis_label = "隐藏悬浮球" if state.get("visible", True) else "显示悬浮球"
    return [
        ("打开管理器页面", "open"),
        ("复制访问地址", "copy"),
        (actions.autostart_label(state.get("autostart", False)), "autostart"),
        ("", ""),
        ("定时关机…", "shutdown"),
        (theme_label, "theme"),
        ("笔记本…", "notes"),
        ("", ""),
        (vis_label, "toggle"),
        ("退出管理器", "quit"),
    ]


_FAB_CLASS = None


def _fab_class():
    """惰性构建 QWidget 子类（首次调用才 import PyQt6）。"""
    global _FAB_CLASS
    if _FAB_CLASS is not None:
        return _FAB_CLASS

    from PyQt6.QtCore import QPoint, QRectF, QSize, Qt
    from PyQt6.QtGui import (QBrush, QColor, QIcon, QPainter, QPen,
                             QRadialGradient)
    from PyQt6.QtWidgets import QApplication, QMenu, QWidget

    class FabBall(QWidget):
        def __init__(self, host, port, token="", server_dead=None):
            super().__init__()
            self.host = host
            self.port = int(port)
            self.token = token or ""
            self.server_dead = server_dead
            self._hover = False
            self._press = None
            self._moved = False
            self._pix = QIcon(qt_theme.icon_path()).pixmap(QSize(_ICON, _ICON))

            self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint
                                | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setFixedSize(_BALL, _BALL)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setMouseTracking(True)
            self.setToolTip(actions.ui_url(self.host, self.port, self.token))
            self._place_initial()

        # ---------------- 位置 ----------------
        def _screen_wh(self):
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else None
            if g is None:
                return (1920, 1080)
            return (g.width(), g.height())

        def _place_initial(self):
            from ..core import config
            sw, sh = self._screen_wh()
            x, y = pos.resolve_pos(config.settings.get("fab_pos", ""),
                                   (sw, sh), size=_BALL, margin=_MARGIN)
            self.move(int(x), int(y))

        def _persist_pos(self):
            sw, sh = self._screen_wh()
            x, y = pos.clamp_pos(self.x(), self.y(), (sw, sh),
                                 size=_BALL, margin=_MARGIN)
            self.move(int(x), int(y))
            actions.save_flag("fab_pos", pos.fmt_pos(x, y))

        # ---------------- 绘制 ----------------
        def paintEvent(self, ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(_MARGIN, _MARGIN, _BALL - 2 * _MARGIN, _BALL - 2 * _MARGIN)
            pal = qt_theme.palette()

            # 1) 柔和投影（多层半透明椭圆，向下偏移）
            p.setPen(Qt.PenStyle.NoPen)
            for i, alpha in ((3, 14), (1, 22), (0, 30)):
                p.setBrush(QColor(0, 0, 0, alpha))
                p.drawEllipse(r.adjusted(-i, -i + 2, i, i + 2))

            # 2) hover 光晕（在图标之下，避免糊住猫脸）
            if self._hover:
                halo = QRadialGradient(r.center(), r.width() * 0.78)
                c = QColor(pal["accent"])
                c.setAlpha(110)
                halo.setColorAt(0.0, c)
                halo.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setBrush(QBrush(halo))
                p.drawEllipse(r.adjusted(-4, -4, 4, 4))

            # 3) 球体：优先猫图标，缺失回退渐变圆 + 「设」
            if not self._pix.isNull():
                cxp = int(r.center().x() - _ICON / 2)
                cyp = int(r.center().y() - _ICON / 2)
                p.drawPixmap(cxp, cyp, self._pix)
            else:
                grad = QRadialGradient(r.center() - QPoint(int(r.width() * 0.22),
                                                           int(r.height() * 0.25)),
                                       r.width() * 1.25)
                grad.setColorAt(0.0, QColor("#a9c4e8"))
                grad.setColorAt(1.0, QColor(pal["accent"]))
                p.setBrush(QBrush(grad))
                p.drawEllipse(r)
                p.setPen(QPen(QColor("#ffffff")))
                f = p.font()
                f.setPixelSize(20)
                p.setFont(f)
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "设")
            p.end()

        # ---------------- 鼠标 ----------------
        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._press = (e.globalPosition().toPoint(), self.pos())
                self._moved = False
                e.accept()
                return
            super().mousePressEvent(e)

        def mouseMoveEvent(self, e):
            if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton):
                gp = e.globalPosition().toPoint()
                d = gp - self._press[0]
                if abs(d.x()) > 4 or abs(d.y()) > 4:
                    self._moved = True
                self.move(self._press[1] + d)
                e.accept()
                return
            super().mouseMoveEvent(e)

        def mouseReleaseEvent(self, e):
            if e.button() != Qt.MouseButton.LeftButton:
                super().mouseReleaseEvent(e)
                return
            moved = self._moved
            self._press = None
            if moved:
                self._persist_pos()
            else:
                actions.open_ui(self.host, self.port, self.token)   # 左键单击 → 开网页
            e.accept()

        def enterEvent(self, e):
            self._hover = True
            self.update()

        def leaveEvent(self, e):
            self._hover = False
            self.update()

        # ---------------- 右键菜单 ----------------
        def contextMenuEvent(self, e):
            self._show_menu(e.globalPos())

        def _state(self):
            from ..core import config
            return {
                "autostart": actions.autostart_enabled(),
                "visible": self.isVisible(),
                "theme_label": actions.ui_theme_label(),
            }

        def _show_menu(self, gpos):
            m = QMenu(self)
            for label, key in menu_entries(self._state()):
                if not label:
                    m.addSeparator()
                    continue
                act = m.addAction(label)
                act.triggered.connect(
                    lambda checked=False, k=key: self._on_action(k))
            m.exec(gpos)

        def _on_action(self, key):
            if key == "open":
                actions.open_ui(self.host, self.port, self.token)
            elif key == "copy":
                actions.copy_url(self.host, self.port, self.token)
            elif key == "autostart":
                actions.toggle_autostart()
            elif key == "shutdown":
                mins = qt_theme.ask_minutes(
                    self, "定时关机", "多少分钟后关机？（0 = 立即，最多 1440）",
                    1, 0, 1440)
                if mins is not None:
                    actions.schedule_shutdown(self.host, self.port, int(mins) * 60)
            elif key == "theme":
                self._toggle_theme()
            elif key == "notes":
                from . import qt_notes
                qt_notes.open_notes(self.host, self.port)
            elif key == "toggle":
                self.set_visible(False)
            elif key == "quit":
                actions.quit_app(self.host, self.port)

        def _toggle_theme(self):
            from ..core import config
            cur = str(config.settings.get("ui_theme", "light")).strip().lower()
            nxt = "dark" if cur != "dark" else "light"
            got = actions.set_ui_theme(self.host, self.port, nxt)
            if got:
                config.settings["ui_theme"] = got
                app = QApplication.instance()
                if app is not None:
                    qt_theme.apply(app)          # 让 Qt 弹窗/菜单即时跟随明暗
                self.update()

        # ---------------- 显隐（托盘「显示悬浮球」调用） ----------------
        def set_visible(self, visible):
            if visible:
                self._place_initial()
                self.show()
                actions.save_flag("fab_visible", True)
            else:
                self.hide()
                actions.save_flag("fab_visible", False)

    _FAB_CLASS = FabBall
    return FabBall


def create(host, port, token="", server_dead=None, fab_visible=True):
    """创建悬浮球；fab_visible=False 时只建不显示（供托盘随时拉起）。"""
    cls = _fab_class()
    ball = cls(host, port, token=token, server_dead=server_dead)
    if fab_visible:
        ball.show()
    return ball
