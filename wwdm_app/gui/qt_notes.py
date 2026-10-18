# -*- coding: utf-8 -*-
"""PyQt6 笔记编辑器（v6.3）：水彩玻璃风，带 Markdown 工具栏与预览。

功能
----
  * 左栏：搜索 + 新建/刷新 + 标签筛选 + 笔记列表（📌置顶、[标签]）；
  * 右栏：标题 / 标签·颜色 / Markdown 工具栏 / 编辑器⇄预览 / 字数与保存状态；
  * 操作：保存(Ctrl+S) / 新建(Ctrl+N) / 搜索(Ctrl+F) / 关闭(Esc，有改动会确认) /
    置顶 / 删除(带确认)；
  * 数据走回环 HTTP ``/api/notes``（兼容网页端的 Markdown 纯文本存储）。

铁律：顶层不 import PyQt6；QWidget 子类在 :func:`_impl` 内定义。
"""
import html
import re
import threading

from . import qt_theme

_COLOR_DEFAULT = "#4a6fa5"
# (显示名, 颜色) —— 下拉框显示中文名，取色用 hex
_COLOR_OPTIONS = [
    ("水彩蓝", "#4a6fa5"),
    ("水彩紫", "#c38d94"),
    ("水彩绿", "#2d8f83"),
    ("水彩琥珀", "#d4a373"),
    ("水彩红", "#d96c6c"),
]


def _http_json(host, port, path, method="GET", payload=None):
    """回环 GET/POST/PUT/DELETE 取 JSON（禁代理）。失败返回 None。"""
    import json
    import urllib.request
    url = "http://%s:%d%s" % (host, port, path)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _note_label(item):
    """列表一行文案（纯函数，便于无 Qt 测试）。"""
    pin = "\U0001F4CC " if item.get("pinned") else ""
    title = item.get("title") or "未命名笔记"
    tag = item.get("tag") or ""
    return pin + title + (("  [" + tag + "]") if tag else "")


# ============================================================
# Markdown 渲染（纯函数，无 Qt，可单测）
# ============================================================
def _md_to_html(text):
    """极简 Markdown → HTML（覆盖网页笔记本支持的语法子集）。"""
    s = html.escape(text or "")
    s = re.sub(r"```(.*?)```",
               lambda m: "<pre>" + m.group(1).strip("\n") + "</pre>",
               s, flags=re.S)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"^### (.+)$", r"<h3>\1</h3>", s, flags=re.M)
    s = re.sub(r"^## (.+)$", r"<h2>\1</h2>", s, flags=re.M)
    s = re.sub(r"^# (.+)$", r"<h1>\1</h1>", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s, flags=re.S)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s, flags=re.S)
    s = re.sub(r"^&gt; (.+)$", r"<blockquote>\1</blockquote>", s, flags=re.M)
    s = re.sub(r"^- (.+)$", r"<li>\1</li>", s, flags=re.M)
    s = re.sub(r"^\d+\. (.+)$", r"<li>\1</li>", s, flags=re.M)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"^---+$", r"<hr>", s, flags=re.M)
    s = s.replace("\n", "<br>")
    return s


def _md_render(text):
    """带水彩配色的预览 HTML（QTextBrowser 用）。"""
    p = qt_theme.palette()
    css = ("<style>"
           "body{font-family:'Microsoft YaHei';color:%s;font-size:10.5pt;line-height:1.65}"
           "h1,h2,h3{color:%s;margin:10px 0 6px}"
           "code{background:%s;border-radius:4px;padding:1px 4px}"
           "pre{background:%s;padding:10px;border-radius:8px}"
           "blockquote{border-left:3px solid %s;margin:6px 0;padding-left:10px;color:%s}"
           "a{color:%s}hr{border:none;border-top:1px solid %s}"
           "</style>") % (p["text"], p["accent"], p["panel2"], p["panel2"],
                          p["accent"], p["text2"], p["accent"], p["border"])
    return css + _md_to_html(text)


_IMPL = None


def _impl():
    """惰性构建（首次调用才 import PyQt6）。返回窗口类。"""
    global _IMPL
    if _IMPL is not None:
        return _IMPL

    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtGui import QIcon, QKeySequence, QShortcut, QTextCursor
    from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QPushButton,
                                 QSplitter, QStackedWidget, QTextBrowser,
                                 QTextEdit, QVBoxLayout, QWidget)

    G = qt_theme.ui_classes()
    GlassCard = G["GlassCard"]
    WashBackground = G["WashBackground"]

    class _Bridge(QObject):
        done = pyqtSignal(str, object)

    class NotesWindow(QWidget):
        def __init__(self, host, port):
            super().__init__(None)
            self.host = host
            self.port = int(port)
            self._items = []
            self._cur_id = None
            self._dirty = False
            self._scuts = []
            self._bridge = _Bridge()
            self._bridge.done.connect(self._on_done)

            self.setWindowTitle("笔记本")
            self.setWindowIcon(QIcon(qt_theme.icon_path()))
            self.setMinimumSize(780, 520)
            self._size_to_screen()
            self._build_ui()
            self._apply_theme()
            qt_theme.on_theme_change(self._apply_theme)
            self._shortcuts()

        def _apply_theme(self):
            """滚动区(QTextEdit/QTextBrowser/QListWidget)不走 QSS 背景，改调色板强制。"""
            from PyQt6.QtGui import QColor, QPalette
            p = qt_theme.palette()
            for wd in (self.body, self.preview, self.list):
                pal = wd.palette()
                pal.setColor(QPalette.ColorRole.Base, QColor(p["panel"]))
                pal.setColor(QPalette.ColorRole.Text, QColor(p["text"]))
                wd.setPalette(pal)
                try:
                    vp = wd.viewport()
                    vp.setPalette(pal)
                    vp.setAutoFillBackground(True)
                except Exception:
                    pass
            try:
                self._wash.update()
            except Exception:
                pass
            for w in self.findChildren(QWidget):
                w.update()

        def _size_to_screen(self):
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else None
            sw, sh = (g.width(), g.height()) if g is not None else (1280, 800)
            w = max(820, min(1160, int(sw * 0.58)))
            h = max(540, min(820, int(sh * 0.68)))
            self.resize(w, h)
            if g is not None:
                self.move(g.x() + (g.width() - w) // 2,
                          g.y() + (g.height() - h) // 2)

        # ---------------- UI ----------------
        def _build_ui(self):
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            self._wash = WashBackground(self)
            outer.addWidget(self._wash)
            root = QVBoxLayout(self._wash)
            root.setContentsMargins(16, 14, 16, 16)
            root.setSpacing(12)

            header = QHBoxLayout()
            header.setSpacing(10)
            ic = QLabel()
            pm = QIcon(qt_theme.icon_path()).pixmap(26, 26)
            if not pm.isNull():
                ic.setPixmap(pm)
            ic.setFixedSize(26, 26)
            header.addWidget(ic, 0)
            t = QLabel("笔记本")
            t.setObjectName("DlgTitle")
            header.addWidget(t, 0)
            header.addStretch(1)
            hint = QLabel("Ctrl+S 保存 · Ctrl+N 新建 · Ctrl+F 搜索")
            hint.setObjectName("Hint")
            header.addWidget(hint)
            root.addLayout(header)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setChildrenCollapsible(False)
            root.addWidget(splitter, 1)

            # ---- 左栏（玻璃卡）----
            left = GlassCard(radius=16)
            lv = QVBoxLayout(left)
            lv.setContentsMargins(12, 12, 12, 12)
            lv.setSpacing(8)
            srow = QHBoxLayout()
            srow.setSpacing(6)
            self.search = QLineEdit()
            self.search.setPlaceholderText("搜索标题/正文/标签…")
            self.search.setClearButtonEnabled(True)
            self.search.returnPressed.connect(lambda: self._load_list(False))
            srow.addWidget(self.search, 1)
            b_new = QPushButton("新建")
            b_new.clicked.connect(self._new)
            b_ref = QPushButton("刷新")
            b_ref.setObjectName("Ghost")
            b_ref.clicked.connect(lambda: self._load_list(False))
            srow.addWidget(b_new)
            srow.addWidget(b_ref)
            lv.addLayout(srow)

            frow = QHBoxLayout()
            frow.setSpacing(6)
            fl = QLabel("标签")
            fl.setObjectName("Hint")
            frow.addWidget(fl)
            self.tag_filter = QComboBox()
            self.tag_filter.addItem("全部标签", "")
            self.tag_filter.setMinimumWidth(130)
            self.tag_filter.currentIndexChanged.connect(
                lambda _i: self._load_list(False))
            frow.addWidget(self.tag_filter, 1)
            lv.addLayout(frow)

            self.list = QListWidget()
            self.list.currentRowChanged.connect(self._on_select)
            lv.addWidget(self.list, 1)
            splitter.addWidget(left)

            # ---- 右栏（玻璃卡）----
            right = GlassCard(radius=16)
            rv = QVBoxLayout(right)
            rv.setContentsMargins(12, 12, 12, 12)
            rv.setSpacing(8)
            self.title_edit = QLineEdit()
            self.title_edit.setObjectName("NoteTitle")
            self.title_edit.setPlaceholderText("标题")
            self.title_edit.textChanged.connect(self._on_edit)
            rv.addWidget(self.title_edit)

            meta = QHBoxLayout()
            meta.setSpacing(8)
            ml = QLabel("标签")
            ml.setObjectName("Hint")
            meta.addWidget(ml)
            self.tag_edit = QLineEdit()
            self.tag_edit.setPlaceholderText("可选")
            self.tag_edit.textChanged.connect(self._on_edit)
            meta.addWidget(self.tag_edit, 1)
            cl = QLabel("颜色")
            cl.setObjectName("Hint")
            meta.addWidget(cl)
            self.color_box = QComboBox()
            for _name, _hex in _COLOR_OPTIONS:
                self.color_box.addItem(_name, _hex)
            self.color_box.setMinimumWidth(100)
            self.color_box.currentIndexChanged.connect(self._on_edit)
            meta.addWidget(self.color_box)
            rv.addLayout(meta)

            # ---- Markdown 工具栏 ----
            tb = QHBoxLayout()
            tb.setSpacing(1)

            def tbtn(label, tip, fn):
                b = QPushButton(label)
                b.setObjectName("Tool")
                b.setToolTip(tip)
                b.setFixedHeight(26)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(fn)
                tb.addWidget(b)
                return b

            tbtn("B", "加粗", lambda: self._insert("**", "**"))
            tbtn("I", "斜体", lambda: self._insert("*", "*"))
            tbtn("S", "删除线", lambda: self._insert("~~", "~~"))
            tbtn("H1", "标题 1", lambda: self._insert("# ", "", True))
            tbtn("H2", "标题 2", lambda: self._insert("## ", "", True))
            tbtn("H3", "标题 3", lambda: self._insert("### ", "", True))
            tbtn("\u2022", "无序列表", lambda: self._insert("- ", "", True))
            tbtn("1.", "有序列表", lambda: self._insert("1. ", "", True))
            tbtn("引", "引用", lambda: self._insert("> ", "", True))
            tbtn("`", "行内代码", lambda: self._insert("`", "`"))
            tbtn("</>", "代码块", lambda: self._insert("```\n", "\n```", True))
            tbtn("链", "链接", lambda: self._insert("[", "](https://)"))
            tbtn("\u2014", "分隔线", lambda: self._insert("\n---\n", "", True))
            tb.addStretch(1)
            self.preview_btn = QPushButton("预览")
            self.preview_btn.setObjectName("Tool")
            self.preview_btn.setCheckable(True)
            self.preview_btn.setFixedHeight(26)
            self.preview_btn.toggled.connect(self._toggle_preview)
            tb.addWidget(self.preview_btn)
            rv.addLayout(tb)

            self.stack = QStackedWidget()
            self.body = QTextEdit()
            self.body.setPlaceholderText("在这里写笔记…支持 Markdown 语法")
            self.body.setAcceptRichText(False)
            self.body.textChanged.connect(self._on_edit)
            self.preview = QTextBrowser()
            self.preview.setObjectName("Preview")
            self.preview.setOpenExternalLinks(True)
            self.stack.addWidget(self.body)
            self.stack.addWidget(self.preview)
            rv.addWidget(self.stack, 1)

            foot = QHBoxLayout()
            foot.setSpacing(8)
            self.status = QLabel("就绪")
            self.status.setObjectName("Hint")
            foot.addWidget(self.status)
            self.dirty = QLabel("")
            self.dirty.setObjectName("Dirty")
            foot.addWidget(self.dirty)
            self.count = QLabel("0 字")
            self.count.setObjectName("Hint")
            foot.addWidget(self.count)
            foot.addStretch(1)
            self.pin_btn = QPushButton("置顶")
            self.pin_btn.setObjectName("Ghost")
            self.pin_btn.clicked.connect(self._pin)
            b_del = QPushButton("删除")
            b_del.setObjectName("Ghost")
            b_del.clicked.connect(self._delete)
            b_save = QPushButton("保存")
            b_save.clicked.connect(self._save)
            foot.addWidget(self.pin_btn)
            foot.addWidget(b_del)
            foot.addWidget(b_save)
            rv.addLayout(foot)
            splitter.addWidget(right)

            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 3)
            splitter.setSizes([300, 720])

        def _shortcuts(self):
            for seq, fn in (("Ctrl+S", self._save),
                            ("Ctrl+N", self._new),
                            ("Ctrl+F", lambda: self.search.setFocus()),
                            ("Esc", self.close)):
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(fn)
                self._scuts.append(sc)

        # ---------------- 跨线程回 UI ----------------
        def _post(self, kind, payload):
            self._bridge.done.emit(kind, payload)

        def _on_done(self, kind, payload):
            if kind == "list":
                self._render_list(payload.get("data"), payload.get("autoload", True))
            elif kind == "note":
                self._fill_editor(payload)
            elif kind == "created":
                self._after_create(payload)
            elif kind == "saved":
                self._after_save(payload)
            elif kind == "deleted":
                self._after_delete(payload)
            elif kind == "pinned":
                self._after_pin(payload)

        # ---------------- 列表 ----------------
        def _load_list(self, autoload=True):
            kw = (self.search.text() or "").strip()
            tag = ""
            try:
                tag = self.tag_filter.currentData() or ""
            except Exception:
                tag = ""

            def work():
                import urllib.parse
                path = "/api/notes?sort=updated_desc"
                if kw:
                    path += "&q=" + urllib.parse.quote(kw)
                if tag:
                    path += "&tag=" + urllib.parse.quote(tag)
                data = _http_json(self.host, self.port, path)
                self._post("list", {"data": data, "autoload": autoload})
            threading.Thread(target=work, daemon=True).start()

        def _render_list(self, data, autoload=True):
            self.list.blockSignals(True)
            self.list.clear()
            if not data:
                self._items = []
                self.list.addItem("服务未启动或无法连接")
                self.status.setText("服务不可达")
                self.list.blockSignals(False)
                return
            self._fill_tags(data.get("tags") or [])
            items = data.get("items") or []
            self._items = items
            if not items:
                self.list.addItem("（暂无笔记，点「新建」）")
                self.status.setText("暂无笔记")
                self.list.blockSignals(False)
                return
            idx = 0
            for i, it in enumerate(items):
                item = QListWidgetItem(_note_label(it))
                item.setData(Qt.ItemDataRole.UserRole, it.get("id"))
                self.list.addItem(item)
                if self._cur_id is not None and it.get("id") == self._cur_id:
                    idx = i
            self.list.setCurrentRow(idx)
            self.list.blockSignals(False)
            if autoload:
                self._load_note(items[idx].get("id"))

        def _fill_tags(self, tags):
            self.tag_filter.blockSignals(True)
            cur = ""
            try:
                cur = self.tag_filter.currentData() or ""
            except Exception:
                cur = ""
            self.tag_filter.clear()
            self.tag_filter.addItem("全部标签", "")
            for tg in tags:
                self.tag_filter.addItem(tg, tg)
            i = self.tag_filter.findData(cur)
            self.tag_filter.setCurrentIndex(i if i >= 0 else 0)
            self.tag_filter.blockSignals(False)

        def _on_select(self, row):
            if row is None or row < 0 or row >= len(self._items):
                return
            rid = self._items[row].get("id")
            if rid is not None:
                self._load_note(rid)

        # ---------------- 单篇 ----------------
        def _load_note(self, rid):
            if rid is None:
                return
            self._cur_id = rid

            def work():
                data = _http_json(self.host, self.port, "/api/notes/%d" % int(rid))
                self._post("note", data)
            threading.Thread(target=work, daemon=True).start()

        def _fill_editor(self, data):
            if not data or not data.get("ok"):
                self.status.setText("读取失败")
                return
            n = data.get("note") or {}
            content = n.get("content") or ""
            self._cur_id = n.get("id")
            for w in (self.title_edit, self.tag_edit, self.body):
                w.blockSignals(True)
            self.title_edit.setText(n.get("title") or "")
            self.tag_edit.setText(n.get("tag") or "")
            self.body.setPlainText(content)
            for w in (self.title_edit, self.tag_edit, self.body):
                w.blockSignals(False)
            color = n.get("color") or _COLOR_DEFAULT
            cid = self.color_box.findData(color)
            self.color_box.blockSignals(True)
            if cid < 0:
                self.color_box.addItem(color, color)
                cid = self.color_box.findData(color)
            self.color_box.setCurrentIndex(cid)
            self.color_box.blockSignals(False)
            self.pin_btn.setText("取消置顶" if n.get("pinned") else "置顶")
            self._dirty = False
            self.dirty.setText("")
            self.count.setText("%d 字" % len(content))
            self.status.setText("已载入")
            if self.preview_btn.isChecked():
                self.preview.setHtml(_md_render(content))

        # ---------------- 编辑辅助 ----------------
        def _on_edit(self, *a):
            self._dirty = True
            self.dirty.setText("\u25CF 未保存")
            try:
                self.count.setText("%d 字" % len(self.body.toPlainText()))
            except Exception:
                pass

        def _insert(self, before, after="", line_start=False):
            tb = self.body
            cur = tb.textCursor()
            if line_start:
                text = tb.toPlainText()
                s = cur.selectionStart()
                e = cur.selectionEnd()
                ls = text.rfind("\n", 0, s) + 1
                le = text.find("\n", e)
                if le == -1:
                    le = len(text)
                block = text[ls:le]
                new = "\n".join(before + ln for ln in block.split("\n"))
                cur.setPosition(ls)
                cur.setPosition(le, QTextCursor.MoveMode.KeepAnchor)
                cur.insertText(new)
            else:
                sel = cur.selectedText().replace("\u2029", "\n")
                cur.insertText(before + sel + after)
                if not sel:
                    cur.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
            tb.setTextCursor(cur)
            tb.setFocus()

        def _toggle_preview(self, on):
            if on:
                self.preview.setHtml(_md_render(self.body.toPlainText()))
                self.stack.setCurrentWidget(self.preview)
                self.preview_btn.setText("编辑")
            else:
                self.stack.setCurrentWidget(self.body)
                self.preview_btn.setText("预览")

        # ---------------- 增删改 ----------------
        def _collect(self):
            return {
                "title": (self.title_edit.text().strip() or "未命名笔记"),
                "content": self.body.toPlainText(),
                "tag": self.tag_edit.text().strip(),
                "color": (self.color_box.currentData() or _COLOR_DEFAULT),
            }

        def _new(self):
            payload = {"title": "未命名笔记", "content": "",
                       "color": _COLOR_DEFAULT, "tag": ""}

            def work():
                data = _http_json(self.host, self.port, "/api/notes",
                                  method="POST", payload=payload)
                self._post("created", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_create(self, data):
            if not data or not data.get("ok"):
                self.status.setText("新建失败")
                return
            self._cur_id = data.get("id")
            self.status.setText("已新建")
            self._load_note(self._cur_id)
            self._load_list(autoload=False)

        def _save(self):
            payload = self._collect()
            cur = self._cur_id
            if cur is None:
                def work_create():
                    data = _http_json(self.host, self.port, "/api/notes",
                                      method="POST", payload=payload)
                    self._post("created", data)
                threading.Thread(target=work_create, daemon=True).start()
                return
            self.status.setText("保存中…")

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d" % int(cur), method="PUT",
                                  payload=payload)
                self._post("saved", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_save(self, data):
            if not data or not data.get("ok"):
                self.status.setText("保存失败")
                return
            self._dirty = False
            self.dirty.setText("")
            self.status.setText("已保存")
            self._load_list(autoload=False)

        def _delete(self):
            cur = self._cur_id
            if cur is None:
                self.status.setText("未选中笔记")
                return
            if not qt_theme.confirm(self, "删除笔记",
                                    "确定删除这篇笔记？此操作不可撤销。",
                                    ok_text="删除"):
                return

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d" % int(cur), method="DELETE")
                self._post("deleted", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_delete(self, data):
            if not data or not data.get("ok"):
                self.status.setText("删除失败")
                return
            self._cur_id = None
            for w in (self.title_edit, self.tag_edit, self.body):
                w.blockSignals(True)
            self.title_edit.setText("")
            self.tag_edit.setText("")
            self.body.setPlainText("")
            for w in (self.title_edit, self.tag_edit, self.body):
                w.blockSignals(False)
            self._dirty = False
            self.dirty.setText("")
            self.status.setText("已删除")
            self._load_list(autoload=True)

        def _pin(self):
            cur = self._cur_id
            if cur is None:
                self.status.setText("未选中笔记")
                return

            def work():
                data = _http_json(self.host, self.port,
                                  "/api/notes/%d/pin" % int(cur), method="POST")
                self._post("pinned", data)
            threading.Thread(target=work, daemon=True).start()

        def _after_pin(self, data):
            if not data or not data.get("ok"):
                self.status.setText("置顶失败")
                return
            self.status.setText("已切换置顶")
            self._load_list(autoload=False)
            if self._cur_id is not None:
                self._load_note(self._cur_id)

        # ---------------- 关闭 ----------------
        def closeEvent(self, e):
            if self._dirty:
                if not qt_theme.confirm(self, "未保存",
                                        "有未保存的修改，确定关闭笔记本？",
                                        ok_text="关闭"):
                    e.ignore()
                    return
            e.accept()

    _IMPL = NotesWindow
    return NotesWindow


_NOTES_WINS = {}


def open_notes(host, port):
    """打开/前置笔记弹窗（按 host:port 单实例）。需在 GUI 线程调用。"""
    key = "%s:%d" % (host, int(port))
    w = _NOTES_WINS.get(key)
    cls = _impl()
    if w is None:
        w = cls(host, port)
        _NOTES_WINS[key] = w
        w._load_list(autoload=True)
    elif not w.isVisible():
        w._load_list(autoload=True)
    w.show()
    w.raise_()
    w.activateWindow()
    return w
