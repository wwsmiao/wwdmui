# -*- coding: utf-8 -*-
"""笔记本路由（v5.0 第 6 步 · 域 2/10）。

从 ``routes.py`` 的 ``register(app)`` 里整段搬来，只做了三件事：

1. ``@app.route`` → ``@bp.route``，去掉一级缩进；
2. ``db.notes_init_db()``（原本是 register() 期的副作用）改成 ``@bp.record_once``，
   保持「每个 app 注册时跑一次」的语义；
3. 端点名从 ``api_notes_*`` 变成 ``notes.api_notes_*``（全仓无 ``url_for``，安全）。

**本域没有 ``services/notes.py``**：笔记的读写逻辑全在 ``core/db.py`` 的
``notes_*`` 系列里，路由层是纯转发，没有可拆的业务代码 —— 不为凑齐 10 个文件
造一层空壳。将来若把笔记逻辑从 db 层抽出来，再建 ``services/notes.py``。
"""
from flask import Blueprint, jsonify, request

from .. import database as db

bp = Blueprint("notes", __name__)


@bp.record_once
def _notes_init(state):
    """等价于拆前 register() 里的 db.notes_init_db()：**每个 app 注册时跑一次**。

    用 record_once 而不是模块级直接调，是为了保住「create_app() 调几次就初始化几次」
    的原语义（模块级只会随 import 跑一次）。
    """
    db.notes_init_db()

@bp.route("/api/notes", methods=["GET"])
def api_notes_list():
    kw = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    sort = request.args.get("sort", "updated_desc").strip()
    items = db.notes_get_all(kw, tag, sort)
    return jsonify({"ok": True, "items": items, "tags": db.notes_get_tags()})

@bp.route("/api/notes", methods=["POST"])
def api_notes_add():
    d = request.get_json(force=True, silent=True) or {}
    title = (d.get("title") or "未命名笔记").strip()[:200]
    content = d.get("content") or ""
    color = d.get("color") or "#00d4ff"
    tag = (d.get("tag") or "").strip()[:50]
    rid = db.notes_add(title, content, color, tag)
    return jsonify({"ok": True, "id": rid})

@bp.route("/api/notes/<int:rid>", methods=["GET"])
def api_notes_get(rid):
    row = db.notes_get(rid)
    return jsonify({"ok": True, "note": row}) if row else (jsonify({"ok": False, "msg": "笔记不存在"}), 404)

@bp.route("/api/notes/<int:rid>", methods=["PUT"])
def api_notes_update(rid):
    d = request.get_json(force=True, silent=True) or {}
    note = db.notes_get(rid)
    if not note:
        return jsonify({"ok": False, "msg": "笔记不存在"}), 404
    title = (d.get("title") if d.get("title") is not None else note["title"]).strip()[:200]
    content = d.get("content") if d.get("content") is not None else note["content"]
    color = d.get("color") or note["color"]
    tag = (d.get("tag") if d.get("tag") is not None else note["tag"]).strip()[:50]
    db.notes_update(rid, title, content, color, tag)
    return jsonify({"ok": True})

@bp.route("/api/notes/<int:rid>", methods=["DELETE"])
def api_notes_delete(rid):
    db.notes_delete(rid)
    return jsonify({"ok": True})

@bp.route("/api/notes/<int:rid>/pin", methods=["POST"])
def api_notes_pin(rid):
    db.notes_toggle_pin(rid)
    return jsonify({"ok": True})
