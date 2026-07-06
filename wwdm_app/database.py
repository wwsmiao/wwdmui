import os, sqlite3
from .config import DB_PATH

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
