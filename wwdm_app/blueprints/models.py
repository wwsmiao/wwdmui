# -*- coding: utf-8 -*-
"""模型库 API 蓝图（v5.0 第 6 步 域 10/11）。

模型库的增删改查全部落在 database 层（db.db_*），services 不参与，
所以本域**只搬路由，services 零改动**。
"""
from flask import Blueprint, jsonify, request

from .. import database as db
from .. import services as svc

bp = Blueprint("models", __name__)


# ========== 模型 API ==========
@bp.route("/api/models", methods=["GET"])
def api_list():
    kw = request.args.get("q", "").strip()
    sort = request.args.get("sort", "id_asc").strip()
    page = request.args.get("page", type=int, default=1)
    page_size = request.args.get("page_size", type=int, default=20)
    page_size = max(min(page_size, 500), 10)
    offset = (page - 1) * page_size
    total = db.db_model_count(kw)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
        offset = (page - 1) * page_size
    items = db.db_search_paginated(kw, sort, page_size, offset)
    return jsonify({"items": items, "total": total, "page": page,
                    "page_size": page_size, "total_pages": total_pages})

@bp.route("/api/models", methods=["POST"])
def api_add():
    d = request.get_json(force=True, silent=True)
    if not d or not d.get("name") or not d.get("save_path") or not d.get("download_url"):
        return jsonify({"ok": False, "msg": "参数不完整"}), 400
    rid = db.db_add(d["name"].strip(), d["save_path"].strip(), d["download_url"].strip())
    return jsonify({"ok": True, "id": rid}) if rid else (jsonify({"ok": False, "msg": "URL已存在"}), 400)

@bp.route("/api/models/extract_names", methods=["POST"])
def api_extract_names():
    d = request.get_json(force=True, silent=True)
    if not d or not isinstance(d.get("urls"), str):
        return jsonify({"ok": False, "msg": "请提供urls"}), 400
    urls = [l.strip() for l in d["urls"].splitlines() if l.strip()]
    regex = d.get("regex", "").strip()
    if not urls:
        return jsonify({"ok": False, "msg": "请至少输入一个链接"}), 400
    results = []
    import re as _re
    _pattern = None
    if regex:
        try:
            _pattern = _re.compile(regex, _re.IGNORECASE)
        except _re.error as e:
            return jsonify({"ok": False, "msg": f"正则表达式错误: {e}"}), 400
    for url in urls:
        name = ""
        if _pattern:
            m = _pattern.search(url)
            if m:
                name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
        results.append({"url": url, "name": name})
    return jsonify({"ok": True, "results": results})

@bp.route("/api/models/batch", methods=["POST"])
def api_batch_add():
    d = request.get_json(force=True, silent=True)
    if not d or not isinstance(d.get("items"), list) or len(d["items"]) == 0:
        return jsonify({"ok": False, "msg": "未提供模型数据"}), 400
    save_path = d.get("save_path", "").strip()
    if not save_path:
        return jsonify({"ok": False, "msg": "请填写存放路径"}), 400
    items = []
    for entry in d["items"]:
        url = entry.get("url", "").strip()
        name = entry.get("name", "").strip()
        if not url or not name:
            continue
        items.append({"name": name, "save_path": save_path, "download_url": url})
    if not items:
        return jsonify({"ok": False, "msg": "没有有效的模型条目"}), 400
    result = db.db_add_batch(items)
    return jsonify({"ok": True, "added": result["added"], "skipped": result["skipped"]})

@bp.route("/api/models/<int:rid>", methods=["GET"])
def api_get(rid):
    row = db.db_get(rid)
    return jsonify(row) if row else (jsonify({"error": "not found"}), 404)

@bp.route("/api/models/<int:rid>", methods=["PUT"])
def api_update(rid):
    d = request.get_json(force=True, silent=True)
    if not d:
        return jsonify({"ok": False}), 400
    db.db_update(rid, d["name"].strip(), d["save_path"].strip(), d["download_url"].strip())
    return jsonify({"ok": True})

@bp.route("/api/models/<int:rid>", methods=["DELETE"])
def api_delete(rid):
    row = db.db_get(rid)          # 先取出来，删完就只有 id 了，审计要记名字
    db.db_delete(rid)
    svc.audit("model.delete", target=(row or {}).get("name") or ("#%d" % rid),
              detail=(row or {}).get("save_path") or "")
    return jsonify({"ok": True})

@bp.route("/api/paths", methods=["GET"])
def api_paths():
    return jsonify(db.db_get_paths())
