import os, sqlite3
from .config import DB_PATH, NOTES_DB_PATH

# ---- 排序映射 ----
SORT_MAP = {
    "id_asc": "id ASC",
    "id_desc": "id DESC",
    "name_asc": "name COLLATE NOCASE ASC",
    "name_desc": "name COLLATE NOCASE DESC",
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 模型 CRUD
# ============================================================
def db_model_count(keyword=""):
    conn = get_db()
    try:
        if keyword:
            row = conn.execute(
                "SELECT COUNT(*) FROM models WHERE name LIKE ? OR save_path LIKE ? OR download_url LIKE ?",
                ("%" + keyword + "%",) * 3
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM models").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def db_search_paginated(keyword, sort="id_asc", limit=50, offset=0):
    order = SORT_MAP.get(sort, SORT_MAP["id_asc"])
    conn = get_db()
    try:
        if keyword:
            rows = conn.execute(
                "SELECT * FROM models WHERE name LIKE ? OR save_path LIKE ? OR download_url LIKE ? ORDER BY " + order + " LIMIT ? OFFSET ?",
                ("%" + keyword + "%",) * 3 + (limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM models ORDER BY " + order + " LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_search(keyword, sort="id_asc"):
    order = SORT_MAP.get(sort, SORT_MAP["id_asc"])
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM models WHERE name LIKE ? OR save_path LIKE ? OR download_url LIKE ? ORDER BY " + order,
            ("%" + keyword + "%",) * 3
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def db_get(rid):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM models WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def db_add(name, save_path, download_url):
    conn = get_db()
    try:
        conn.execute("INSERT INTO models (name, save_path, download_url) VALUES (?, ?, ?)",
                     (name, save_path, download_url))
        conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return rid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def db_update(rid, name, save_path, download_url):
    conn = get_db()
    try:
        conn.execute("UPDATE models SET name=?, save_path=?, download_url=? WHERE id=?",
                     (name, save_path, download_url, rid))
        conn.commit()
    finally:
        conn.close()


def db_add_batch(items):
    """批量插入模型，跳过重复URL。返回 {"added": n, "skipped": n}"""
    conn = get_db()
    added = 0
    skipped = 0
    try:
        for item in items:
            try:
                conn.execute(
                    "INSERT INTO models (name, save_path, download_url) VALUES (?, ?, ?)",
                    (item["name"], item["save_path"], item["download_url"])
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
        return {"added": added, "skipped": skipped}
    finally:
        conn.close()


def db_delete(rid):
    conn = get_db()
    try:
        conn.execute("DELETE FROM models WHERE id=?", (rid,))
        conn.commit()
    finally:
        conn.close()


def db_get_paths():
    conn = get_db()
    try:
        rows = conn.execute("SELECT DISTINCT save_path FROM models ORDER BY save_path").fetchall()
        return [r["save_path"] for r in rows]
    finally:
        conn.close()


# ============================================================
# 插件 CRUD（nodes 表）
# ============================================================
def plugin_search(kw, sort="id_asc", limit=200, offset=0):
    conn = get_db()
    try:
        order = SORT_MAP.get(sort, "id ASC")
        if kw:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE name LIKE ? OR description LIKE ? OR url LIKE ? ORDER BY " + order + " LIMIT ? OFFSET ?",
                ("%" + kw + "%",) * 3 + (limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nodes ORDER BY " + order + " LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def plugin_count(kw):
    conn = get_db()
    try:
        if kw:
            row = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE name LIKE ? OR description LIKE ? OR url LIKE ?",
                ("%" + kw + "%",) * 3
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
        return row[0]
    finally:
        conn.close()


def plugin_get(pid):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def plugin_add(name, url, description=""):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO nodes (name, description, url) VALUES (?,?,?)",
            (name, description, url)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def plugin_update(pid, name, url, description=""):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE nodes SET name=?, description=?, url=? WHERE id=?",
            (name, description, url, pid)
        )
        conn.commit()
    finally:
        conn.close()


def plugin_delete(pid):
    conn = get_db()
    try:
        conn.execute("DELETE FROM nodes WHERE id=?", (pid,))
        conn.commit()
    finally:
        conn.close()


def plugin_get_names():
    """检测 custom_nodes 下已安装的插件"""
    from .services import get_comfyui_dir
    cn_dir = os.path.join(get_comfyui_dir(), "custom_nodes")
    if not os.path.isdir(cn_dir):
        return {}
    installed = {}
    for entry in os.listdir(cn_dir):
        full = os.path.join(cn_dir, entry)
        if os.path.isdir(full) and os.path.exists(os.path.join(full, ".git")):
            installed[entry] = full
    return installed


# ============================================================
# 笔记本 CRUD（notes.db）
# ============================================================
def _get_notes_db():
    conn = sqlite3.connect(NOTES_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def notes_init_db():
    """创建笔记本表（如不存在）"""
    conn = sqlite3.connect(NOTES_DB_PATH)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '未命名笔记',
            content TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#00d4ff',
            tag TEXT NOT NULL DEFAULT '',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        conn.commit()
    finally:
        conn.close()


def notes_get_all(keyword="", tag="", sort="updated_desc"):
    """获取笔记列表，支持搜索/标签过滤/排序"""
    conn = _get_notes_db()
    try:
        sql = "SELECT * FROM notes"
        conds = []
        args = []
        if keyword:
            conds.append("(title LIKE ? OR content LIKE ? OR tag LIKE ?)")
            args += ["%" + keyword + "%"] * 3
        if tag:
            conds.append("tag = ?")
            args.append(tag)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        order = {
            "updated_desc": "updated_at DESC",
            "updated_asc": "updated_at ASC",
            "created_desc": "created_at DESC",
            "created_asc": "created_at ASC",
            "title_asc": "title COLLATE NOCASE ASC",
        }.get(sort, "updated_at DESC")
        sql += " ORDER BY pinned DESC, " + order
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def notes_get(rid):
    conn = _get_notes_db()
    try:
        row = conn.execute("SELECT * FROM notes WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def notes_add(title, content, color, tag):
    conn = _get_notes_db()
    try:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute(
            "INSERT INTO notes (title, content, color, tag, pinned, created_at, updated_at) VALUES (?,?,?,?,0,?,?)",
            (title, content, color, tag, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def notes_update(rid, title, content, color, tag):
    conn = _get_notes_db()
    try:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE notes SET title=?, content=?, color=?, tag=?, updated_at=? WHERE id=?",
            (title, content, color, tag, now, rid)
        )
        conn.commit()
    finally:
        conn.close()


def notes_delete(rid):
    conn = _get_notes_db()
    try:
        conn.execute("DELETE FROM notes WHERE id=?", (rid,))
        conn.commit()
    finally:
        conn.close()


def notes_toggle_pin(rid):
    conn = _get_notes_db()
    try:
        conn.execute("UPDATE notes SET pinned = 1 - pinned WHERE id=?", (rid,))
        conn.commit()
    finally:
        conn.close()


def notes_get_tags():
    """获取所有已使用的标签"""
    conn = _get_notes_db()
    try:
        rows = conn.execute("SELECT DISTINCT tag FROM notes WHERE tag != '' ORDER BY tag").fetchall()
        return [r["tag"] for r in rows]
    finally:
        conn.close()
