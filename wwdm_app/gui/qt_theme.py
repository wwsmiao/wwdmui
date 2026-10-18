# -*- coding: utf-8 -*-
"""PyQt6 主题（v6.3）：网页水彩配色 → Qt 样式表 + 水彩玻璃弹窗基类。

视觉：水彩晕染底（多枚低透明径向色斑）+ 半透明玻璃卡片 + 圆角 + 柔和投影 +
猫图标标题。只依赖 PyQt6 自带能力（Fusion + QSS + QPainter），不引入额外包。

铁律：**顶层不 import PyQt6**（无 Qt 的测试解释器也要能 import 本模块做纯逻辑测试）。
"""

_LIGHT = {
    "bg":      "#faf8f5",
    "panel":   "#ffffff",
    "panel2":  "#f7f3ec",
    "text":    "#3a3a3a",
    "text2":   "#6b7280",
    "accent":  "#4a6fa5",
    "accent2": "#6f92bd",
    "green":   "#2d8f83",
    "red":     "#d96c6c",
    "amber":   "#d4a373",
    "purple":  "#c38d94",
    "teal":    "#7fb6a8",
    "border":  "#e6e0d6",
    "field":   "#f5f1ea",                 # 输入区（浅）
    "font":    "Microsoft YaHei",
}
_DARK = {
    "bg":      "#171a21",
    "panel":   "#232833",
    "panel2":  "#2a303d",
    "text":    "#d5dbe5",
    "text2":   "#9aa5b5",
    "accent":  "#8fb3e0",
    "accent2": "#6f92bd",
    "green":   "#6ec5b8",
    "red":     "#e88a8a",
    "amber":   "#e0b98f",
    "purple":  "#d8a3aa",
    "teal":    "#8fc7bb",
    "border":  "#3a4150",
    "field":   "#2a303d",                 # 输入区（深）
    "font":    "Microsoft YaHei",
}


def palette():
    """当前调色板（按 ``config.ui_theme`` 明暗）。"""
    from ..core import config
    theme = str(config.settings.get("ui_theme", "light")).strip().lower()
    return _DARK if theme == "dark" else _LIGHT


def is_dark(pal=None):
    pal = pal or palette()
    return pal.get("bg") == _DARK["bg"]


def icon_path():
    """统一图标（exe / 托盘 / 悬浮球 / 网页 favicon）的 .ico 路径。"""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # wwdm_app
    return os.path.join(base, "static", "images", "app.ico")


def qss():
    """生成 Qt 样式表（跟随明暗）。"""
    p = palette()
    return """
    * {
        font-family: "%(font)s", "Segoe UI", "PingFang SC", sans-serif;
        font-size: 10pt;
        outline: none;
    }
    QWidget { color: %(text)s; }
    QMenu {
        background: %(panel)s; border: 1px solid %(border)s;
        border-radius: 12px; padding: 6px;
    }
    QMenu::item { padding: 8px 26px 8px 20px; border-radius: 7px; color: %(text)s; }
    QMenu::item:selected { background: %(accent)s; color: #ffffff; }
    QMenu::item:disabled { color: %(text2)s; }
    QMenu::separator { height: 1px; background: %(border)s; margin: 5px 10px; }
    QPushButton {
        background: %(accent)s; color: #ffffff; border: none;
        border-radius: 9px; padding: 8px 16px; font-weight: 600;
    }
    QPushButton:hover { background: %(accent2)s; }
    QPushButton:pressed { background: %(accent2)s; }
    QPushButton:disabled { background: %(panel2)s; color: %(text2)s; }
    QLineEdit, QComboBox {
        background: %(field)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 9px; padding: 6px 9px;
        selection-background-color: %(accent)s; selection-color: #ffffff;
    }
    QTextEdit {
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 10px; padding: 8px;
        selection-background-color: %(accent)s; selection-color: #ffffff;
    }
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid %(accent)s; }
    QListWidget {
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 11px; padding: 5px;
    }
    QListWidget::item { padding: 7px 9px; border-radius: 7px; }
    QListWidget::item:selected { background: %(accent)s; color: #ffffff; }
    QListWidget::item:hover { background: %(panel2)s; }
    QComboBox QAbstractItemView {
        background: %(panel)s; color: %(text)s; border: 1px solid %(border)s;
        selection-background-color: %(accent)s; selection-color: #ffffff;
    }
    QLabel { background: transparent; }
    QDialog, QMessageBox { background: %(bg)s; }
    QDialog#CardDlg { background: transparent; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
    QScrollBar::handle:vertical { background: %(border)s; border-radius: 5px; min-height: 26px; }
    QScrollBar::handle:vertical:hover { background: %(accent2)s; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
    QScrollBar::handle:horizontal { background: %(border)s; border-radius: 5px; min-width: 26px; }
    QScrollBar::handle:horizontal:hover { background: %(accent2)s; }
    /* ---- 玻璃卡片弹窗 / 笔记工具栏 ---- */
    QPushButton#Tool {
        background: transparent; color: %(text)s; border: none;
        border-radius: 6px; padding: 3px 7px; font-weight: 600;
    }
    QPushButton#Tool:hover { background: %(panel2)s; }
    QPushButton#Tool:checked { background: %(accent)s; color: #ffffff; }
    QPushButton#Chip {
        background: transparent; color: %(text)s; border: 1px solid %(border)s;
        border-radius: 13px; padding: 4px 13px; font-weight: 500;
    }
    QPushButton#Chip:hover { background: %(panel2)s; border-color: %(accent)s; }
    QPushButton#Chip:checked { background: %(accent)s; color: #ffffff; border-color: %(accent)s; }
    QPushButton#DyCard { background: transparent; border: 2px solid transparent; border-radius: 10px; padding: 2px; }
    QPushButton#DyCard:hover { border-color: %(border)s; }
    QPushButton#DyCard:checked { border-color: %(accent)s; }
    QTextBrowser#Preview { border: none; }
    QLabel#DlgTitle { font-size: 12pt; font-weight: 700; color: %(text)s; }
    QLabel#Hint { color: %(text2)s; font-size: 9pt; }
    QLabel#Dirty { color: %(amber)s; font-size: 9pt; }
    QLabel#DlgBody { color: %(text)s; }
    QLineEdit#NoteTitle { font-size: 13pt; font-weight: 600; border: none; background: transparent; }
    QLineEdit#NoteSearch, QTextEdit#NoteBody, QListWidget#NoteList { border: none; }
    QPushButton#DlgClose {
        background: transparent; border: none; color: %(text2)s;
        font-size: 12pt; border-radius: 7px;
    }
    QPushButton#DlgClose:hover { background: %(panel2)s; color: %(red)s; }
    QPushButton#Ghost {
        background: transparent; color: %(text)s;
        border: 1px solid %(border)s; font-weight: 500;
    }
    QPushButton#Ghost:hover { background: %(panel2)s; }
    QSpinBox {
        background: %(field)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 9px; padding: 6px 9px;
        min-height: 22px;
    }
    QSpinBox:focus { border: 1px solid %(accent)s; }
    QSpinBox::up-button, QSpinBox::down-button {
        width: 20px; background: %(panel2)s; border: none;
    }
    QSplitter::handle { background: transparent; }
    QSplitter::handle:horizontal { width: 10px; }
    """ % p


_THEME_LISTENERS = []


def on_theme_change(fn):
    """注册主题切换回调（已打开的窗口据此重刷自绘/调色板控件）。"""
    if fn not in _THEME_LISTENERS:
        _THEME_LISTENERS.append(fn)


def _notify_theme():
    for fn in list(_THEME_LISTENERS):
        try:
            fn()
        except Exception:
            pass


def apply(app):
    """把主题装到 QApplication：Fusion 引擎（QSS 才能完整生效）+ 样式表。"""
    try:
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
        app.setStyleSheet(qss())
        _notify_theme()
        return True
    except Exception:
        return False


# ============================================================
# 水彩玻璃控件（惰性构建；无 Qt 解释器不触发）
# ============================================================
_UI = None


def ui_classes():
    """惰性构建并缓存：GlassCard / WashBackground / GlassDialog 三个类。"""
    global _UI
    if _UI is not None:
        return _UI

    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (QColor, QIcon, QLinearGradient, QPainter,
                             QPainterPath, QPen, QPixmap, QRadialGradient)
    from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect,
                                 QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout, QWidget)

    def _blobs(pal):
        return [
            (0.10, 0.12, 0.60, pal["accent"]),
            (0.88, 0.08, 0.52, pal["purple"]),
            (0.78, 0.92, 0.55, pal["teal"]),
            (0.16, 0.88, 0.48, pal["amber"]),
        ]

    def _paint_blobs(painter, rect, alpha):
        pal = palette()
        for fx, fy, fr, col in _blobs(pal):
            c = QColor(col)
            c.setAlpha(alpha)
            cx = rect.x() + fx * rect.width()
            cy = rect.y() + fy * rect.height()
            grad = QRadialGradient(QPointF(cx, cy), fr * rect.width())
            grad.setColorAt(0.0, c)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(grad)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, cy), fr * rect.width(), fr * rect.width())

    class GlassCard(QFrame):
        """水彩玻璃卡：半透明底 + 水彩斑 + 圆角 + 亮边（自绘，无 QSS 背景）。"""

        def __init__(self, parent=None, radius=18, alpha=None):
            super().__init__(parent)
            self._radius = float(radius)
            self._alpha = alpha
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        def paintEvent(self, ev):
            pal = palette()
            dark = is_dark(pal)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect())
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                self._radius, self._radius)
            painter.setClipPath(path)
            alpha = self._alpha
            if alpha is None:
                alpha = 222 if dark else 232      # 偏实一些：扁平轻量，不发闷
            base = QColor(pal["panel"])
            base.setAlpha(alpha)
            painter.fillRect(rect, base)
            _paint_blobs(painter, rect, 28 if dark else 22)
            painter.setClipping(False)
            # v6.4：不描边 —— 少框少线，靠半透明底与留白区分层级
            painter.end()

    class WashBackground(QWidget):
        """整窗水彩底（不透明渐变 + 色斑），用于笔记窗内容区。"""

        def paintEvent(self, ev):
            pal = palette()
            dark = is_dark(pal)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect())
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, QColor(pal["bg"]))
            grad.setColorAt(1.0, QColor(pal["panel2"]))
            painter.fillRect(rect, grad)
            _paint_blobs(painter, rect, 40 if dark else 34)
            painter.end()

    class GlassDialog(QDialog):
        """无边框玻璃卡片弹窗：水彩晕染 + 投影 + 可拖动 + 内容自适应 + 居中。"""

        def __init__(self, parent, title, subtitle="", show_icon=True):
            super().__init__(parent)
            self.setObjectName("CardDlg")
            self.setWindowIcon(QIcon(icon_path()))
            self.setWindowFlags(Qt.WindowType.Dialog
                                | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._drag = None

            outer = QVBoxLayout(self)
            outer.setContentsMargins(26, 24, 26, 30)      # 给投影留白
            self.card = GlassCard(radius=20)
            shadow = QGraphicsDropShadowEffect(self.card)
            shadow.setBlurRadius(26)
            shadow.setOffset(0, 6)
            shadow.setColor(QColor(0, 0, 0, 55))
            self.card.setGraphicsEffect(shadow)
            outer.addWidget(self.card)

            box = QVBoxLayout(self.card)
            box.setContentsMargins(24, 18, 24, 18)
            box.setSpacing(14)

            head = QHBoxLayout()
            head.setSpacing(10)
            if show_icon:
                ic = QLabel()
                pm = QPixmap(icon_path())
                if not pm.isNull():
                    ic.setPixmap(pm.scaled(
                        26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                ic.setFixedSize(26, 26)
                head.addWidget(ic, 0)
            self.title_label = QLabel(title or "")
            self.title_label.setObjectName("DlgTitle")
            head.addWidget(self.title_label, 1)
            close = QPushButton("\u00D7")                 # ×（各字体都有，避免 tofu）
            close.setObjectName("DlgClose")
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.setFixedSize(28, 28)
            close.clicked.connect(self.reject)
            head.addWidget(close, 0)
            box.addLayout(head)

            if subtitle:
                sub = QLabel(subtitle)
                sub.setObjectName("Hint")
                sub.setWordWrap(True)
                box.addWidget(sub)

            self.body = QVBoxLayout()
            self.body.setSpacing(12)
            box.addLayout(self.body)

            self.buttons = QHBoxLayout()
            self.buttons.setSpacing(10)
            self.buttons.addStretch(1)
            box.addLayout(self.buttons)

            self.setMinimumWidth(380)

        # ---- 自适应居中（有父窗居中于父窗，否则居中屏幕，且不越界）----
        def _center(self):
            self.adjustSize()
            scr = self.screen()
            if scr is None:
                return
            area = scr.availableGeometry()
            w, h = self.width(), self.height()
            par = self.parentWidget()
            if par is not None and par.isVisible() and par.width() > 120:
                g = par.geometry()
                x = g.x() + (g.width() - w) // 2
                y = g.y() + (g.height() - h) // 2
            else:
                x = area.x() + (area.width() - w) // 2
                y = area.y() + (area.height() - h) // 2
            x = min(max(x, area.x() + 8), area.x() + area.width() - w - 8)
            y = min(max(y, area.y() + 8), area.y() + area.height() - h - 8)
            self.move(int(x), int(y))

        def showEvent(self, ev):
            super().showEvent(ev)
            self._center()

        # ---- 无边框拖动 ----
        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._drag = (ev.globalPosition().toPoint(), self.pos())
                ev.accept()

        def mouseMoveEvent(self, ev):
            if self._drag is not None and (ev.buttons() & Qt.MouseButton.LeftButton):
                self.move(self._drag[1]
                          + (ev.globalPosition().toPoint() - self._drag[0]))
                ev.accept()

        def mouseReleaseEvent(self, ev):
            self._drag = None

        def add_button(self, text, primary=False, on_click=None):
            b = QPushButton(text)
            if primary:
                b.setDefault(True)
            else:
                b.setObjectName("Ghost")
                b.setAutoDefault(False)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumWidth(88)
            if on_click is not None:
                b.clicked.connect(on_click)
            self.buttons.addWidget(b)
            return b

    _UI = {"GlassCard": GlassCard, "WashBackground": WashBackground,
           "GlassDialog": GlassDialog}
    return _UI


def ask_minutes(parent, title, prompt, initial=1, minvalue=0, maxvalue=1440):
    """水彩玻璃「多少分钟后关机」弹窗（含猫图标、快捷时长）。返回 int 或 None。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox
    G = ui_classes()
    dlg = G["GlassDialog"](parent, "定时关机",
                           subtitle="到点由管理器自动关机，可随时取消")
    lab = QLabel(prompt)
    lab.setObjectName("DlgBody")
    lab.setWordWrap(True)
    dlg.body.addWidget(lab)

    sp = QSpinBox()
    sp.setRange(int(minvalue), int(maxvalue))
    sp.setValue(int(initial))
    sp.setSuffix("  分钟")
    sp.setMinimumWidth(190)
    dlg.body.addWidget(sp)

    chips = QHBoxLayout()
    chips.setSpacing(8)
    for v in (15, 30, 60, 120):
        b = QPushButton("%d 分钟" % v)
        b.setObjectName("Chip")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, vv=v: sp.setValue(vv))
        chips.addWidget(b)
    chips.addStretch(1)
    dlg.body.addLayout(chips)

    hint = QLabel("提示：0 = 立即关机")
    hint.setObjectName("Hint")
    dlg.body.addWidget(hint)

    result = {"v": None}

    def _ok():
        result["v"] = int(sp.value())
        dlg.accept()

    dlg.add_button("取消", on_click=dlg.reject)
    dlg.add_button("确定", primary=True, on_click=_ok)
    sp.setFocus()
    sp.selectAll()
    dlg.exec()
    return result["v"]


def show_error(parent, title, msg):
    """水彩玻璃错误弹窗。"""
    from PyQt6.QtWidgets import QLabel
    G = ui_classes()
    dlg = G["GlassDialog"](parent, title)
    lab = QLabel(msg)
    lab.setObjectName("DlgBody")
    lab.setWordWrap(True)
    lab.setMinimumWidth(320)
    lab.setMaximumWidth(470)
    dlg.body.addWidget(lab)
    dlg.add_button("确定", primary=True, on_click=dlg.accept)
    dlg.exec()


def confirm(parent, title, msg, ok_text="确定"):
    """水彩玻璃确认弹窗。返回 True/False。"""
    from PyQt6.QtWidgets import QLabel
    G = ui_classes()
    dlg = G["GlassDialog"](parent, title)
    lab = QLabel(msg)
    lab.setObjectName("DlgBody")
    lab.setWordWrap(True)
    lab.setMinimumWidth(300)
    lab.setMaximumWidth(440)
    dlg.body.addWidget(lab)

    res = {"ok": False}

    def _ok():
        res["ok"] = True
        dlg.accept()

    dlg.add_button("取消", on_click=dlg.reject)
    dlg.add_button(ok_text, primary=True, on_click=_ok)
    dlg.exec()
    return res["ok"]
