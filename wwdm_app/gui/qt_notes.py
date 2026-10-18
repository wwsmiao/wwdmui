# -*- coding: utf-8 -*-
"""PyQt6 笔记编辑器（v6.4）：水彩风、**富文本所见即所得**（WYSIWYG）。

功能
----
  * 左栏：搜索框 + 搜索按钮 + 新建/刷新 + 笔记列表（📌置顶）；
  * 右栏：标题 / 富文本工具栏 / 编辑器 / 字数与保存状态 / 置顶·删除·保存；
  * 编辑器所见即所得：加粗、斜体、下划线、删除线、H1-H3、有序/无序列表、
    引用、代码块、插入图片、插入链接、撤销/重做；
  * 去掉了标签与颜色（数据字段仍保留原值，避免把旧笔记的 tag/color 抹掉）；
  * 保存为 HTML（`QTextEdit.toHtml()`）；旧版 Markdown 笔记载入时自动转换；
  * Ctrl+S 保存 / Ctrl+N 新建 / Ctrl+F 搜索 / Esc 关闭（有改动会确认）。

铁律：顶层不 import PyQt6；QWidget 子类在 :func:`_impl` 内定义。
"""
import html
import re
import threading

from . import qt_theme

_COLOR_DEFAULT = "#4a6fa5"      # 旧笔记 color 字段的原值保留用（UI 不再提供改色）


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
    """列表一行文案：置顶标记 + 标题（不再显示标签）。"""
    pin = "\U0001F4CC " if item.get("pinned") else ""
    return pin + (item.get("title") or "未命名笔记")


def _md_to_html(text):
    """极简 Markdown → HTML（仅用于把旧版 Markdown 笔记载入富文本编辑器）。"""
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


def _looks_like_html(text):
    """判断存的是 HTML（新格式）还是旧版 Markdown。"""
    if not text:
        return False
    low = text.lstrip().lower()
    if low.startswith("<!doctype") or low.startswith("<html"):
        return True
    return any(mark in low for mark in (
        "<p ", "<p>", "<body", "<span ", "<div ", "<h1", "<h2", "<h3",
        "<ul", "<ol", "<pre", "<img", "<blockquote"))


_IMPL = None


def _impl():
    """惰性构建（首次调用才 import PyQt6）。返回窗口类。"""
    global _IMPL
    if _IMPL is not None:
        return _IMPL

    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QKeySequence,
                             QShortcut, QTextBlockFormat, QTextCharFormat,
                             QTextCursor, QTextImageFormat, QTextListFormat)
    from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                                 QListWidget, QListWidgetItem, QPushButton,
                                 QSplitter, QTextEdit, QVBoxLayout, QWidget)

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
            self._cur_tag = ""          # 旧字段：保留原值，不在 UI 暴露
            self._cur_color = _COLOR_DEFAULT
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

        def _apply_theme(self):
            """滚动区（QTextEdit/QListWidget）不走 QSS 背景，改调色板强制。"""
            from PyQt6.QtGui import QColor, QPalette
            p = qt_theme.palette()
            for wd in (self.body, self.list):
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

        # ---------------- UI ----------------
        def _build_ui(self):
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            self._wash = WashBackground(self)
            outer.addWidget(self._wash)
            root = QVBoxLayout(self._wash)
            root.setContentsMargins(14, 12, 14, 14)
            root.setSpacing(10)

            header = QHBoxLayout()
            header.setSpacing(8)
            ic = QLabel()
            pm = QIcon(qt_theme.icon_path()).pixmap(24, 24)
            if not pm.isNull():
                ic.setPixmap(pm)
            ic.setFixedSize(24, 24)
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

            # ---- 左栏 ----
            left = GlassCard(radius=14)
            lv = QVBoxLayout(left)
            lv.setContentsMargins(10, 10, 10, 10)
            lv.setSpacing(7)
            srow = QHBoxLayout()
            srow.setSpacing(6)
            self.search = QLineEdit()
            self.search.setObjectName("NoteSearch")
            self.search.setPlaceholderText("搜索标题/正文…")
            self.search.setClearButtonEnabled(True)
            self.search.returnPressed.connect(lambda: self._load_list(False))
            srow.addWidget(self.search, 1)
            b_search = QPushButton("搜索")
            b_search.clicked.connect(lambda: self._load_list(False))
            srow.addWidget(b_search)
            lv.addLayout(srow)

            brow = QHBoxLayout()
            brow.setSpacing(6)
            b_new = QPushButton("新建")
            b_new.clicked.connect(self._new)
            b_ref = QPushButton("刷新")
            b_ref.setObjectName("Ghost")
            b_ref.clicked.connect(lambda: self._load_list(False))
            brow.addWidget(b_new)
            brow.addWidget(b_ref)
            brow.addStretch(1)
            lv.addLayout(brow)

            self.list = QListWidget()
            self.list.setObjectName("NoteList")
            self.list.currentRowChanged.connect(self._on_select)
            lv.addWidget(self.list, 1)
            splitter.addWidget(left)

            # ---- 右栏 ----
            right = GlassCard(radius=14)
            rv = QVBoxLayout(right)
            rv.setContentsMargins(10, 10, 10, 10)
            rv.setSpacing(6)
            self.title_edit = QLineEdit()
            self.title_edit.setObjectName("NoteTitle")
            self.title_edit.setPlaceholderText("标题")
            self.title_edit.textChanged.connect(self._on_edit)
            rv.addWidget(self.title_edit)

            # 富文本工具栏
            tb = QHBoxLayout()
            tb.setSpacing(1)

            def tbtn(label, tip, fn):
                b = QPushButton(label)
                b.setObjectName("Tool")
                b.setToolTip(tip)
                b.setFixedHeight(25)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(fn)
                tb.addWidget(b)
                return b

            tbtn("B", "加粗 (Ctrl+B)", self._toggle_bold)
            tbtn("I", "斜体 (Ctrl+I)", self._toggle_italic)
            tbtn("U", "下划线 (Ctrl+U)", self._toggle_underline)
            tbtn("S", "删除线", self._toggle_strike)
            tb.addSpacing(5)
            tbtn("H1", "标题 1", lambda: self._heading(20))
            tbtn("H2", "标题 2", lambda: self._heading(16))
            tbtn("H3", "标题 3", lambda: self._heading(13))
            tb.addSpacing(5)
            tbtn("\u2022", "无序列表", lambda: self._add_list(
                QTextListFormat.Style.ListDisc))
            tbtn("1.", "有序列表", lambda: self._add_list(
                QTextListFormat.Style.ListDecimal))
            tbtn("引", "引用", self._quote)
            tbtn("码", "代码块", self._code)
            tb.addSpacing(5)
            tbtn("图", "插入图片", self._insert_image)
            tbtn("链", "插入链接", self._insert_link)
            tb.addSpacing(5)
            tbtn("\u21B6", "撤销", lambda: self.body.undo())
            tbtn("\u21B7", "重做", lambda: self.body.redo())
            tb.addStretch(1)
            rv.addLayout(tb)

            self.body = QTextEdit()
            self.body.setObjectName("NoteBody")
            self.body.setAcceptRichText(True)
            self.body.setPlaceholderText("在这里写笔记…可直接加粗、插图、插代码")
            self.body.textChanged.connect(self._on_edit)
            rv.addWidget(self.body, 1)

            foot = QHBoxLayout()
            foot.setSpacing(7)
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
            splitter.setSizes([290, 730])

        def _shortcuts(self):
            for seq, fn in (("Ctrl+S", self._save),
                            ("Ctrl+N", self._new),
                            ("Ctrl+F", lambda: self.search.setFocus()),
                            ("Esc", self.close)):
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(fn)
                self._scuts.append(sc)

        # ---------------- 富文本操作 ----------------
        def _char(self, fmt):
            cur = self.body.textCursor()
            cur.mergeCharFormat(fmt)
            self.body.mergeCurrentCharFormat(fmt)
            self.body.setFocus()

        def _toggle_bold(self):
            f = QTextCharFormat()
            f.setFontWeight(400 if self.body.fontWeight() >= 600 else 700)
            self._char(f)

        def _toggle_italic(self):
            f = QTextCharFormat()
            f.setFontItalic(not self.body.fontItalic())
            self._char(f)

        def _toggle_underline(self):
            f = QTextCharFormat()
            f.setFontUnderline(not self.body.fontUnderline())
            self._char(f)

        def _toggle_strike(self):
            f = QTextCharFormat()
            f.setFontStrikeOut(
                not self.body.currentCharFormat().fontStrikeOut())
            self._char(f)

        def _heading(self, pt):
            f = QTextCharFormat()
            f.setFontPointSize(float(pt))
            f.setFontWeight(700)
            cur = self.body.textCursor()
            cur.select(QTextCursor.SelectionType.BlockUnderCursor)
            cur.mergeCharFormat(f)
            self.body.mergeCurrentCharFormat(f)
            self.body.setFocus()

        def _add_list(self, style):
            cur = self.body.textCursor()
            cur.createList(style)
            self.body.setFocus()

        def _quote(self):
            cur = self.body.textCursor()
            bf = QTextBlockFormat()
            bf.setLeftMargin(22)
            bf.setIndent(1)
            cur.mergeBlockFormat(bf)
            f = QTextCharFormat()
            f.setFontItalic(True)
            cur.select(QTextCursor.SelectionType.BlockUnderCursor)
            cur.mergeCharFormat(f)
            self.body.setFocus()

        def _code(self):
            p = qt_theme.palette()
            cur = self.body.textCursor()
            bf = QTextBlockFormat()
            try:
                bf.setBackground(QBrush(QColor(p["panel2"])))
            except Exception:
                pass
            bf.setLeftMargin(10)
            bf.setRightMargin(10)
            cur.mergeBlockFormat(bf)
            f = QTextCharFormat()
            try:
                f.setFontFamilies(["Consolas", "Courier New", "monospace"])
            except Exception:
                try:
                    f.setFontFamily("Consolas")
                except Exception:
                    pass
            f.setFontFixedPitch(True)
            f.setForeground(QColor(p["accent2"]))
            cur.select(QTextCursor.SelectionType.BlockUnderCursor)
            cur.mergeCharFormat(f)
            self.body.mergeCurrentCharFormat(f)
            self.body.setFocus()

        def _insert_image(self):
            from PyQt6.QtGui import QImage
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(
                self, "插入图片", "",
                "图片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;所有文件 (*.*)")
            if not path:
                return
            img = QImage(path)
            fmt = QTextImageFormat()
            fmt.setName(path)
            if not img.isNull():
                w = img.width()
                fmt.setWidth(520 if w > 520 else max(1, w))
            cur = self.body.textCursor()
            cur.insertImage(fmt)
            self.body.setFocus()

        def _insert_link(self):
            from PyQt6.QtWidgets import QInputDialog
            url, ok = QInputDialog.getText(
                self, "插入链接", "网址（可含 https://）：", text="https://")
            url = (url or "").strip()
            if not ok or not url:
                return
            cur = self.body.textCursor()
            sel = cur.selectedText()
            f = QTextCharFormat()
            f.setAnchor(True)
            f.setAnchorHref(url)
            f.setForeground(QColor(qt_theme.palette()["accent"]))
            f.setFontUnderline(True)
            if sel:
                cur.mergeCharFormat(f)
            else:
                cur.insertText(url, f)
            self.body.setFocus()

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

            def work():
                import urllib.parse
                path = "/api/notes?sort=updated_desc"
                if kw:
                    path += "&q=" + urllib.parse.quote(kw)
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
            self._cur_tag = n.get("tag") or ""
            self._cur_color = n.get("color") or _COLOR_DEFAULT
            for w in (self.title_edit, self.body):
                w.blockSignals(True)
            self.title_edit.setText(n.get("title") or "")
            if _looks_like_html(content):
                self.body.setHtml(content)
            else:
                self.body.setHtml(_md_to_html(content))
            for w in (self.title_edit, self.body):
                w.blockSignals(False)
            self.pin_btn.setText("取消置顶" if n.get("pinned") else "置顶")
            self._dirty = False
            self.dirty.setText("")
            self.count.setText("%d 字" % len(self.body.toPlainText()))
            self.status.setText("已载入")

        # ---------------- 编辑状态 ----------------
        def _on_edit(self, *a):
            self._dirty = True
            self.dirty.setText("\u25CF 未保存")
            try:
                self.count.setText("%d 字" % len(self.body.toPlainText()))
            except Exception:
                pass

        # ---------------- 增删改 ----------------
        def _collect(self):
            return {
                "title": (self.title_edit.text().strip() or "未命名笔记"),
                "content": self.body.toHtml(),
                "tag": self._cur_tag,          # 旧字段原样带回，避免抹掉
                "color": self._cur_color,
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
            for w in (self.title_edit, self.body):
                w.blockSignals(True)
            self.title_edit.setText("")
            self.body.setHtml("")
            for w in (self.title_edit, self.body):
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
