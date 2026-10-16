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


def db_get_many(ids):
    """批量取模型：一次 IN 查询代替 N 次 db_get（去掉轮询接口的 N+1）"""
    out = {}
    clean = []
    for i in ids:
        try:
            clean.append(int(i))
        except (TypeError, ValueError):
            continue
    if not clean:
        return out
    conn = get_db()
    try:
        # 分块，避开 SQLite 变量数上限
        for i in range(0, len(clean), 500):
            chunk = clean[i:i + 500]
            q = "SELECT * FROM models WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for r in conn.execute(q, chunk).fetchall():
                d = dict(r)
                out[d["id"]] = d
        return out
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
def plugin_search(kw, sort="id_asc", limit=200, offset=0, names=None):
    """names 不为 None 时只返回名字命中的行（把「已安装」过滤下推到 SQL，
    避免把整表 5000+ 行拉进 Python 里过滤分页）"""
    conn = get_db()
    try:
        order = SORT_MAP.get(sort, "id ASC")
        where, args = [], []
        if names is not None:
            if not names:
                return []
            where.append("name IN (%s)" % ",".join("?" * len(names)))
            args += list(names)
        if kw:
            where.append("(name LIKE ? OR description LIKE ? OR url LIKE ?)")
            args += ["%" + kw + "%"] * 3
        sql = "SELECT * FROM nodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + order + " LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def plugin_count(kw, names=None):
    """names 不为 None 时只统计命中的行（配合 plugin_search 的已安装过滤下推）"""
    conn = get_db()
    try:
        where, args = [], []
        if names is not None:
            if not names:
                return 0
            where.append("name IN (%s)" % ",".join("?" * len(names)))
            args += list(names)
        if kw:
            where.append("(name LIKE ? OR description LIKE ? OR url LIKE ?)")
            args += ["%" + kw + "%"] * 3
        sql = "SELECT COUNT(*) FROM nodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        row = conn.execute(sql, args).fetchone()
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


def plugin_get_many(pids):
    """批量取插件：一次 IN 查询代替 N 次 plugin_get（去掉轮询接口的 N+1）"""
    out = {}
    clean = []
    for i in pids:
        try:
            clean.append(int(i))
        except (TypeError, ValueError):
            continue
    if not clean:
        return out
    conn = get_db()
    try:
        for i in range(0, len(clean), 500):
            chunk = clean[i:i + 500]
            q = "SELECT * FROM nodes WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for r in conn.execute(q, chunk).fetchall():
                d = dict(r)
                out[d["id"]] = d
        return out
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
    try:
        # scandir 的 is_dir() 走目录枚举缓存，比 listdir + 逐项 isdir 省一半 stat；
        # 本函数被 /api/plugins 与 30s 一次的横幅计数反复调用
        with os.scandir(cn_dir) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and os.path.exists(os.path.join(e.path, ".git")):
                        installed[e.name] = e.path
                except Exception:
                    continue
    except Exception:
        return installed
    return installed


def _norm_plugin_url(u):
    """规范化插件仓库地址：剥掉常见 GitHub 加速前缀、去 .git、去尾斜杠、统一小写"""
    u = (u or "").strip()
    if not u:
        return ""
    for p in ("https://api.gitproxy.dev/github.com/",
              "https://gh-proxy.com/https://github.com/",
              "https://ghproxy.net/https://github.com/",
              "https://ghproxy.homeboyc.cn/https://github.com/",
              "https://github.akams.cn/https://github.com/",
              "https://gitclone.com/github.com/",
              "https://github.ur1.fun/"):
        if u.startswith(p):
            u = "https://github.com/" + u[len(p):]
            break
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4]
    return u.lower()


def plugin_repair_db():
    """安全修复插件数据库(nodes 表)：
      1) 先备份数据库文件
      2) 去除重复插件：名称与规范化后的仓库地址都相同的记录只保留一条
         （同名但不同仓库属于不同插件，不做合并）
      3) 把 id 重新整理为 1..n 的连续序列
    返回 (ok, result)
    """
    import shutil, time as _time
    backup = ""
    try:
        ts = _time.strftime("%Y%m%d_%H%M%S")
        backup = DB_PATH + "." + ts + ".bak"
        shutil.copy2(DB_PATH, backup)
    except Exception as e:
        return False, {"msg": "备份数据库失败，已中止操作：%s" % e}

    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM nodes ORDER BY id ASC").fetchall()]
        total_before = len(rows)

        groups = {}
        for r in rows:
            key = ((r.get("name") or "").strip().lower(), _norm_plugin_url(r.get("url")))
            groups.setdefault(key, []).append(r)

        def _rank(r):
            u = (r.get("url") or "").strip()
            return (0 if u.startswith("https://github.com/") else 1, r["id"])

        keep, removed = [], []
        for key, grp in groups.items():
            if len(grp) == 1:
                keep.append(grp[0])
                continue
            grp = sorted(grp, key=_rank)
            keeper = grp[0]
            keep.append(keeper)
            for r in grp[1:]:
                removed.append({"id": r["id"], "name": r.get("name"), "url": r.get("url"),
                                "keep_id": keeper["id"]})
        keep.sort(key=lambda r: r["id"])

        conn.execute("DELETE FROM nodes")
        if keep:
            conn.executemany(
                "INSERT INTO nodes (id, name, description, url, created_at) VALUES (?,?,?,?,?)",
                [(i, r.get("name"), r.get("description") or "", r.get("url"), r.get("created_at"))
                 for i, r in enumerate(keep, 1)]
            )
            row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='nodes'").fetchone()
            if row:
                conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name='nodes'", (len(keep),))
            else:
                conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('nodes', ?)", (len(keep),))
        conn.commit()
        return True, {"ok": True, "total_before": total_before, "total_after": len(keep),
                      "removed_count": len(removed), "removed": removed,
                      "backup": backup}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, {"msg": "修复失败，数据库已回滚：%s（备份：%s）" % (e, backup)}
    finally:
        conn.close()


# ============================================================
# 插件库内置数据：node_map（节点类名 → 插件仓库）
# 工作流解析用它把「缺失的节点类名」翻译成「该装哪个插件仓库」。
# 数据随包内置在 models.db 里，**不依赖本机有没有装 ComfyUI-Manager**。
# 更新入口：插件管理页「更新插件库」→ services.plugins_update_library()
#            → db.plugin_upsert_bulk() + db.node_map_replace()
# ============================================================

NODE_MAP_DDL = (
    "CREATE TABLE IF NOT EXISTS node_map ("
    " node TEXT NOT NULL,"
    " url  TEXT NOT NULL,"
    " PRIMARY KEY (node, url)"
    ") WITHOUT ROWID"
)
NODE_MAP_IDX = "CREATE INDEX IF NOT EXISTS idx_node_map_url ON node_map(url)"


def node_map_ensure(conn=None):
    """确保 node_map 表与索引存在（老库自动升级）。"""
    own = conn is None
    if own:
        conn = get_db()
    try:
        conn.execute(NODE_MAP_DDL)
        conn.execute(NODE_MAP_IDX)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def node_map_replace(pairs, conn=None):
    """整表重建 node_map。pairs 为 (node, url) 可迭代对象，返回写入条数。

    这张表是上游快照的派生物，没有用户手工维护的内容，所以直接重建而不是逐条合并。
    """
    own = conn is None
    if own:
        conn = get_db()
    try:
        conn.execute(NODE_MAP_DDL)
        conn.execute(NODE_MAP_IDX)
        conn.execute("DELETE FROM node_map")
        n = 0
        batch = []

        def _flush():
            if batch:
                conn.executemany("INSERT OR IGNORE INTO node_map (node, url) VALUES (?,?)", batch)
                batch[:] = []

        for node, url in pairs:
            if not node or not url:
                continue
            batch.append((str(node), str(url)))
            n += 1
            if len(batch) >= 5000:
                _flush()
        _flush()
        if own:
            conn.commit()
        return n
    finally:
        if own:
            conn.close()


def node_map_stats():
    conn = get_db()
    try:
        conn.execute(NODE_MAP_DDL)
        pairs = conn.execute("SELECT COUNT(*) FROM node_map").fetchone()[0]
        nodes = conn.execute("SELECT COUNT(DISTINCT node) FROM node_map").fetchone()[0]
        urls = conn.execute("SELECT COUNT(DISTINCT url) FROM node_map").fetchone()[0]
        return {"pairs": pairs, "nodes": nodes, "urls": urls}
    finally:
        conn.close()


def node_map_load():
    """返回 (rev, byurl)：
       rev    {节点类名: [仓库URL, ...]}        原始 URL，界面可直接当链接
       byurl  {规范化URL: {节点类名, ...}}      便于按已装插件反查
    """
    conn = get_db()
    try:
        conn.execute(NODE_MAP_DDL)
        rev, byurl = {}, {}
        for node, url in conn.execute("SELECT node, url FROM node_map"):
            rev.setdefault(node, []).append(url)
            k = _norm_plugin_url(url)
            if k:
                byurl.setdefault(k, set()).add(node)
        return rev, byurl
    finally:
        conn.close()


def plugin_url_titles():
    """{规范化URL: 插件名}，取自 nodes 表，用于给缺失插件显示可读名称。"""
    conn = get_db()
    try:
        out = {}
        for name, url in conn.execute("SELECT name, url FROM nodes"):
            k = _norm_plugin_url(url)
            if k and name:
                out.setdefault(k, name)
        return out
    finally:
        conn.close()


def plugin_ids_by_url():
    """{规范化URL: (id, name)}，工作流解析用它把「缺少的插件」对上数据库条目，
    界面上的下载按钮才能复用既有的、按 id 的克隆流程（含进度/队列/镜像）。

    同址多行时按 id 升序取第一条，与 plugin_upsert_bulk 的口径一致。
    """
    conn = get_db()
    try:
        out = {}
        for pid, name, url in conn.execute("SELECT id, name, url FROM nodes ORDER BY id"):
            k = _norm_plugin_url(url)
            if k and k not in out:
                out[k] = (pid, name or "")
        return out
    finally:
        conn.close()


def plugin_upsert_bulk(items):
    """按【规范化仓库地址】合并写入 nodes 表（用户指定的写库规则）：
         · 地址已存在 → 原地覆盖 name / description（保持原 id 与 created_at）
         · 地址不存在 → 新增一条
       全程不会产生重复地址的记录，也不会删除用户手动添加的插件。

    items: [(name, url, description), ...]
    返回 {"added","updated","unchanged","total"}；updated 只统计字段真的变了的。
    """
    conn = get_db()
    try:
        existing = {}
        for r in conn.execute("SELECT id, name, description, url FROM nodes"):
            k = _norm_plugin_url(r[3])
            if k and k not in existing:
                existing[k] = (r[0], r[1] or "", r[2] or "", r[3] or "")

        added = updated = unchanged = 0
        ins, upd = [], []
        seen = set()
        for name, url, desc in items:
            url = (url or "").strip()
            if not url:
                continue
            k = _norm_plugin_url(url)
            if not k or k in seen:
                continue
            seen.add(k)
            name = (name or "").strip() or k.rstrip("/").split("/")[-1]
            desc = (desc or "").strip()
            hit = existing.get(k)
            if hit is None:
                ins.append((name, desc, url))
                added += 1
            else:
                oid, oname, odesc, ourl = hit
                if (oname, odesc, ourl) != (name, desc, url):
                    upd.append((name, desc, url, oid))
                    updated += 1
                else:
                    unchanged += 1

        if ins:
            conn.executemany("INSERT INTO nodes (name, description, url) VALUES (?,?,?)", ins)
        if upd:
            conn.executemany("UPDATE nodes SET name=?, description=?, url=? WHERE id=?", upd)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        return {"added": added, "updated": updated, "unchanged": unchanged, "total": total}
    finally:
        conn.close()

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
