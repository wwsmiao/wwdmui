# -*- coding: utf-8 -*-
"""tkinter 美化主题（v6.0(7)）：让悬浮球/托盘的弹窗与菜单**跟随网页水彩主题**。

为什么单独成模块
----------------
gui 铁律：顶层不碰 tkinter、不 import services/blueprints。本模块只在函数体内
懒 import tkinter / ttk；颜色在调用时按 ``config.ui_theme``(light/dark) 读取，
使 tk 弹窗与网页明暗同步（网页切夜间，弹窗也夜间）。

配色取自 ``static/style.css`` 的 watercolor 预设（默认皮肤）：
  * light : --bg #faf8f5 / --panel #fff / --text #3a3a3a / --cyan #4a6fa5 / --cyan2 #6f92bd
  * dark  : --bg #171a21 / --panel #232833 / --text #d5dbe5 / --cyan #8fb3e0 / --cyan2 #6f92bd
语义色（成功/失败/警告）跨皮肤稳定，单独列出。
"""
# 🔴 顶层严禁 import tkinter / ttk —— 缓到函数里（无 tkinter 的测试解释器也能 import 本模块）。

# 网页水彩主题明暗两套调色板（键名与 style.css 变量对应，只取 tk 能用的几个）。
_LIGHT = {
    "bg":       "#faf8f5",   # --bg        窗口/面板外底色
    "panel":    "#ffffff",   # --panel     卡片/控件底
    "panel2":   "#f7f3ec",   # --panel2    次面板（表头/槽）
    "text":     "#3a3a3a",   # --text      主文字
    "text2":    "#6b7280",   # --text2     次文字
    "accent":   "#4a6fa5",   # --cyan      主强调（按钮/选中）
    "accent2":  "#6f92bd",   # --cyan2     强调悬停/渐变尾
    "green":    "#2d8f83",   # --green     成功
    "red":      "#d96c6c",   # --red       失败
    "amber":    "#d4a373",   # --amber     警告/提示
    "border":   "#e6e0d6",   # color-mix 近似（--cyan 20%）
    "font":     "Microsoft YaHei",
}
_DARK = {
    "bg":       "#171a21",
    "panel":    "#232833",
    "panel2":   "#2a303d",
    "text":     "#d5dbe5",
    "text2":    "#9aa5b5",
    "accent":   "#8fb3e0",
    "accent2":  "#6f92bd",
    "green":    "#6ec5b8",
    "red":      "#e88a8a",
    "amber":    "#e0b98f",
    "border":   "#3a4150",
    "font":     "Microsoft YaHei",
}


def palette():
    """返回当前应使用的调色板（按 ``config.ui_theme`` 明暗）。"""
    from ..core import config
    theme = str(config.settings.get("ui_theme", "light")).strip().lower()
    return _DARK if theme == "dark" else _LIGHT


def _font(size=10, bold=False):
    p = palette()
    return (p["font"], size, "bold" if bold else "normal")


def menu_options():
    """悬浮球右键菜单 ``tk.Menu`` 的 configure 参数（跟随明暗）。"""
    p = palette()
    return {
        "bg": p["panel"],
        "fg": p["text"],
        "activebackground": p["accent"],
        "activeforeground": "#ffffff",
        "font": _font(10),
        "relief": "flat",
        "borderwidth": 1,
        "activeborderwidth": 1,
    }


def apply_style(root):
    """给整棵 tkinter 树挂上网页水彩风格的 ttk 样式（基于 clam 引擎）。

    幂等：重复调用只重配，不会叠加主题。只动 ttk 控件（按钮/树/标签/进度条）；
    Canvas 画的悬浮球不受影响。应在 root 建好后尽早调用一次（:func:`gui.run`
    与 :meth:`FabBall.build` 都已接）。
    """
    p = palette()
    try:
        import tkinter as tk  # noqa: F401  （仅用于异常兜底与类型提示）
        from tkinter import ttk
        style = ttk.Style(root)
        try:
            style.theme_use("clam")     # clam 引擎允许完全自定义配色（Windows 原生 vista 会忽略多数选项）
        except Exception:
            pass
        # ---- 按钮（对话框/任务中心的操作键）----
        style.configure("TButton",
                        background=p["accent"], foreground="#ffffff",
                        borderwidth=0, relief="flat",
                        font=_font(10), padding=(12, 6))
        style.map("TButton",
                  background=[("active", p["accent2"]),
                              ("pressed", p["accent2"])],
                  foreground=[("active", "#ffffff")])
        # ---- 标签 / 框架（面板底）----
        style.configure("TLabel",
                        background=p["panel"], foreground=p["text"],
                        font=_font(10))
        style.configure("TFrame", background=p["panel"])
        # ---- 树（任务中心列表）----
        style.configure("Treeview",
                        background=p["panel"], foreground=p["text"],
                        fieldbackground=p["panel"], borderwidth=0,
                        font=_font(10), rowheight=28)
        style.map("Treeview",
                  background=[("selected", p["accent"])],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading",
                        background=p["panel2"], foreground=p["text"],
                        borderwidth=1, relief="flat",
                        font=_font(10, bold=True))
        # ---- 进度条（备用）----
        style.configure("TProgressbar",
                        background=p["accent"], troughcolor=p["panel2"],
                        borderwidth=0)
        try:
            root.configure(background=p["bg"])
        except Exception:
            pass
        return style
    except Exception:
        return None


def _center(parent, win, w, h):
    """把 Toplevel 相对父窗居中（父为 None 时相对屏幕）。"""
    try:
        if parent is not None and getattr(parent, "winfo_exists", lambda: False)():
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        else:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
        win.geometry("%dx%d+%d+%d" % (w, h, x, y))
    except Exception:
        pass


def ask_minutes(parent, title, prompt, initial=1, minvalue=0, maxvalue=1440):
    """网页风格「多少分钟后关机」对话框，返回 int 或 None（取消/非法）。

    取代原 ``tkinter.simpledialog.askinteger``（默认原生样式与网页主题割裂）。
    主线程调用（菜单命令本来就在主线程）。
    """
    import tkinter as tk
    from tkinter import ttk
    p = palette()
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.grab_set()
    dlg.resizable(False, False)
    dlg.configure(background=p["bg"])
    f = ttk.Frame(dlg, padding=(20, 18))
    f.pack(fill="both", expand=True)

    ttk.Label(f, text=prompt, font=_font(10)).pack(anchor="w", pady=(0, 10))

    var = tk.IntVar(value=initial)
    try:
        sb = ttk.Spinbox(f, from_=minvalue, to=maxvalue, textvariable=var,
                         width=12, font=_font(12))
    except Exception:
        sb = ttk.Entry(f, textvariable=var, width=12, font=_font(12))
    sb.pack(anchor="w", pady=(0, 14))

    result = {"val": None}

    def ok():
        try:
            v = int(var.get())
            if minvalue <= v <= maxvalue:
                result["val"] = v
        except Exception:
            pass
        dlg.destroy()

    def cancel():
        dlg.destroy()

    bf = ttk.Frame(f)
    bf.pack(fill="x")
    ttk.Button(bf, text="确定", command=ok).pack(side="right")
    ttk.Button(bf, text="取消", command=cancel).pack(side="right", padx=(0, 8))

    dlg.bind("<Return>", lambda e: ok())
    dlg.bind("<Escape>", lambda e: cancel())
    _center(parent, dlg, 280, 150)
    dlg.wait_window(dlg)
    return result["val"]


def show_error(parent, title, msg):
    """网页风格错误弹窗（取代 ``messagebox.showerror`` 原生样式）。"""
    import tkinter as tk
    from tkinter import ttk
    p = palette()
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.grab_set()
    dlg.resizable(False, False)
    dlg.configure(background=p["bg"])
    f = ttk.Frame(dlg, padding=(20, 18))
    f.pack(fill="both", expand=True)
    ttk.Label(f, text=msg, wraplength=320, justify="left",
              font=_font(10)).pack(anchor="w", pady=(0, 16))
    bf = ttk.Frame(f)
    bf.pack(fill="x")
    ttk.Button(bf, text="确定", command=dlg.destroy).pack(side="right")
    _center(parent, dlg, 360, 150)
    dlg.wait_window(dlg)
