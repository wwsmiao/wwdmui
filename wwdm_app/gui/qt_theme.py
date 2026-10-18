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
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: %(border)s; border-radius: 5px; min-height: 26px; }}
    QScrollBar::handle:vertical:hover {{ background: %(accent2)s; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
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


def ask_minutes(parent, title, prompt, initial=1, minvalue=0, maxvalue=1440):
    """定时关机「多少分钟后」对话框（Qt 原生，套主题）。返回 int 或 None。"""
    from PyQt6.QtWidgets import QInputDialog
    val, ok = QInputDialog.getInt(parent, title, prompt, int(initial),
                                  int(minvalue), int(maxvalue), 1)
    return int(val) if ok else None


def show_error(parent, title, msg):
    """网页风格错误弹窗。"""
    from PyQt6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(msg)
    box.exec()
