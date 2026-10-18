# -*- coding: utf-8 -*-
"""PyQt6 主题（v6.1）：网页水彩配色 → Qt 样式表（QSS），弹窗/菜单/笔记统一。

设计参考：Material / Fluent 的圆角与悬停语义 + 本仓网页 style.css 的 watercolor
调色板。只依赖 PyQt6 自带能力（Fusion 引擎 + QSS），不引入 qt-material 等额外包，
保证分发体积与视觉都可控。

铁律：**顶层不 import PyQt6**（无 Qt 的测试解释器也要能 import 本模块做纯逻辑测试）。
颜色在调用时按 ``config.ui_theme``(light/dark) 读取，Qt 弹窗与网页明暗同步。
"""

# 网页水彩预设（style.css :root / dark）关键色，只取 Qt 用得到的几个。
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
    "border":  "#e6e0d6",
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
    "border":  "#3a4150",
    "font":    "Microsoft YaHei",
}


def palette():
    """当前调色板（按 ``config.ui_theme`` 明暗）。"""
    from ..core import config
    theme = str(config.settings.get("ui_theme", "light")).strip().lower()
    return _DARK if theme == "dark" else _LIGHT


def icon_path():
    """统一图标（exe / 托盘 / 悬浮球 / 网页 favicon 同一只猫）的 .ico 路径。"""
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "wwdm_cat.ico")


def qss():
    """生成 Qt 样式表（跟随明暗）。"""
    p = palette()
    return """
    * {{
        font-family: "%(font)s", "Segoe UI", "PingFang SC", sans-serif;
        font-size: 10pt;
        outline: none;
    }}
    QWidget {{ color: %(text)s; }}
    QMenu {{
        background: %(panel)s;
        border: 1px solid %(border)s;
        border-radius: 12px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 8px 26px 8px 20px;
        border-radius: 7px;
        color: %(text)s;
    }}
    QMenu::item:selected {{ background: %(accent)s; color: #ffffff; }}
    QMenu::item:disabled {{ color: %(text2)s; }}
    QMenu::separator {{ height: 1px; background: %(border)s; margin: 5px 10px; }}
    QPushButton {{
        background: %(accent)s; color: #ffffff; border: none;
        border-radius: 9px; padding: 8px 16px; font-weight: 600;
    }}
    QPushButton:hover {{ background: %(accent2)s; }}
    QPushButton:pressed {{ background: %(accent2)s; }}
    QPushButton:disabled {{ background: %(panel2)s; color: %(text2)s; }}
    QLineEdit, QTextEdit, QComboBox {{
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 9px;
        padding: 6px 9px;
        selection-background-color: %(accent)s; selection-color: #ffffff;
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border: 1px solid %(accent)s; }}
    QListWidget {{
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 11px; padding: 5px;
    }}
    QListWidget::item {{ padding: 7px 9px; border-radius: 7px; }}
    QListWidget::item:selected {{ background: %(accent)s; color: #ffffff; }}
    QListWidget::item:hover {{ background: %(panel2)s; }}
    QComboBox QAbstractItemView {{
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s;
        selection-background-color: %(accent)s; selection-color: #ffffff;
    }}
    QLabel {{ background: transparent; }}
    QDialog, QMessageBox {{ background: %(bg)s; }}
    QDialog#CardDlg {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: %(border)s; border-radius: 5px; min-height: 26px; }}
    QScrollBar::handle:vertical:hover {{ background: %(accent2)s; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    /* ---- 圆角卡片弹窗（qt_theme.ask_minutes / show_error）---- */
    QFrame#Card {{
        background: %(panel)s;
        border: 1px solid %(border)s;
        border-radius: 16px;
    }}
    QLabel#DlgTitle {{ font-size: 12pt; font-weight: 700; color: %(text)s; }}
    QLineEdit#NoteTitle {{ font-size: 13pt; font-weight: 600; }}
    QLabel#Hint {{ color: %(text2)s; font-size: 9pt; }}
    QLabel#DlgBody {{ color: %(text)s; }}
    QPushButton#DlgClose {{
        background: transparent; border: none; color: %(text2)s;
        font-size: 12pt; border-radius: 7px;
    }}
    QPushButton#DlgClose:hover {{ background: %(panel2)s; color: %(red)s; }}
    QPushButton#Ghost {{
        background: transparent; color: %(text)s;
        border: 1px solid %(border)s; font-weight: 500;
    }}
    QPushButton#Ghost:hover {{ background: %(panel2)s; }}
    QSpinBox {{
        background: %(panel)s; color: %(text)s;
        border: 1px solid %(border)s; border-radius: 9px; padding: 6px 9px;
        min-height: 22px;
    }}
    QSpinBox:focus {{ border: 1px solid %(accent)s; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        width: 20px; background: %(panel2)s; border: none;
    }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 8px; }}
    """ % p


def apply(app):
    """把主题装到 QApplication：Fusion 引擎（QSS 才能完整生效）+ 样式表。"""
    try:
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
        app.setStyleSheet(qss())
        return True
    except Exception:
        return False


_CARD_CLASS = None


def _card_class():
    """惰性构建圆角卡片对话框类（无 Qt 解释器不触发）。"""
    global _CARD_CLASS
    if _CARD_CLASS is not None:
        return _CARD_CLASS

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect,
                                 QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)

    class CardDialog(QDialog):
        """无边框圆角卡片弹窗：柔和投影 + 可拖动 + 内容自适应 + 居中。"""

        def __init__(self, parent, title):
            super().__init__(parent)
            self.setObjectName("CardDlg")
            self.setWindowFlags(Qt.WindowType.Dialog
                                | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            # 只让本对话框透明（全局 QSS 的 QDialog#CardDlg 规则）；
            # 卡片 QFrame#Card 仍由全局 QSS 上色
            self._drag = None

            outer = QVBoxLayout(self)
            outer.setContentsMargins(24, 22, 24, 26)     # 给投影留白
            self.card = QFrame()
            self.card.setObjectName("Card")
            shadow = QGraphicsDropShadowEffect(self.card)
            shadow.setBlurRadius(40)
            shadow.setOffset(0, 10)
            shadow.setColor(QColor(0, 0, 0, 90))
            self.card.setGraphicsEffect(shadow)
            outer.addWidget(self.card)

            box = QVBoxLayout(self.card)
            box.setContentsMargins(24, 18, 24, 18)
            box.setSpacing(14)

            head = QHBoxLayout()
            head.setSpacing(8)
            box.addLayout(head)
            self.title_label = QLabel(title or "")
            self.title_label.setObjectName("DlgTitle")
            head.addWidget(self.title_label, 1)
            close = QPushButton("\u2715")                # ✕
            close.setObjectName("DlgClose")
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.setFixedSize(28, 28)
            close.clicked.connect(self.reject)
            head.addWidget(close, 0)

            self.body = QVBoxLayout()
            self.body.setSpacing(10)
            box.addLayout(self.body)

            self.buttons = QHBoxLayout()
            self.buttons.setSpacing(10)
            self.buttons.addStretch(1)
            box.addLayout(self.buttons)

            self.setMinimumWidth(370)

        # ---- 自适应：按内容调整尺寸并居中（有父窗则居中于父窗）----
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
            # 别把弹窗推到屏幕外
            x = min(max(x, area.x() + 8), area.x() + area.width() - w - 8)
            y = min(max(y, area.y() + 8), area.y() + area.height() - h - 8)
            self.move(int(x), int(y))

        def showEvent(self, e):
            super().showEvent(e)
            self._center()

        # ---- 无边框拖动 ----
        def mousePressEvent(self, e):
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag = (e.globalPosition().toPoint(), self.pos())
                e.accept()

        def mouseMoveEvent(self, e):
            if self._drag is not None and (e.buttons() & Qt.MouseButton.LeftButton):
                self.move(self._drag[1]
                          + (e.globalPosition().toPoint() - self._drag[0]))
                e.accept()

        def mouseReleaseEvent(self, e):
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

    _CARD_CLASS = CardDialog
    return CardDialog


def ask_minutes(parent, title, prompt, initial=1, minvalue=0, maxvalue=1440):
    """圆角卡片式「多少分钟后关机」对话框。返回 int 或 None（取消）。"""
    from PyQt6.QtWidgets import QLabel, QSpinBox
    Card = _card_class()
    dlg = Card(parent, title)
    lab = QLabel(prompt)
    lab.setObjectName("DlgBody")
    lab.setWordWrap(True)
    dlg.body.addWidget(lab)

    sp = QSpinBox()
    sp.setRange(int(minvalue), int(maxvalue))
    sp.setValue(int(initial))
    sp.setSuffix("  分钟")
    sp.setMinimumWidth(180)
    dlg.body.addWidget(sp)

    hint = QLabel("0 = 立即关机")
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
    """圆角卡片式错误弹窗。"""
    from PyQt6.QtWidgets import QLabel
    Card = _card_class()
    dlg = Card(parent, title)
    lab = QLabel(msg)
    lab.setObjectName("DlgBody")
    lab.setWordWrap(True)
    lab.setMinimumWidth(320)
    lab.setMaximumWidth(460)
    dlg.body.addWidget(lab)
    dlg.add_button("确定", primary=True, on_click=dlg.accept)
    dlg.exec()
