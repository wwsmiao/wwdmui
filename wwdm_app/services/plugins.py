# -*- coding: utf-8 -*-
"""插件库域：官方插件库拉取合并 / 按仓库地址补录条目 / 按地址克隆。

自 services/__init__.py 拆出（v5.0 第 6 步 域 4/10），函数体逐字未改。
跨域依赖一律函数内延迟导入（`from . import X`）——既避开包初始化期的循环导入，
又保证「在 services 包上打桩后立刻生效」。
"""
import json
import time


# ComfyUI-Manager 官方插件库数据源（依次尝试，第一个可用即止）
_PLUGIN_LIB_BASES = (
    ("官方源", "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main"),
    ("gh-proxy 加速", "https://gh-proxy.com/https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main"),
    ("ghproxy.net 加速", "https://ghproxy.net/https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main"),
    ("GitCode 镜像", "https://raw.gitcode.com/gh_mirrors/co/ComfyUI-Manager/raw/main"),
)


def _fetch_lib_file(filename, errors, deadline):
    """按候选源依次尝试拉一个插件库文件，返回 (json对象, 命中的源名)。

    数据量 3MB 左右，所以给了较宽的单次读超时；但整体有 deadline 兜底，
    避免多个源同时抽风时把请求线程拖到分钟级。
    """
    import requests
    for label, base in _PLUGIN_LIB_BASES:
        if time.time() > deadline:
            errors.append("已达总超时上限，剩余数据源不再尝试")
            break
        url = base + "/" + filename
        for verify in (True, False):
            try:
                r = requests.get(url, timeout=(5, 30), verify=verify)
                if r.status_code != 200:
                    errors.append("%s · %s：HTTP %d" % (label, filename, r.status_code))
                    break
                obj = json.loads(r.content.decode("utf-8"))
                if not obj:
                    errors.append("%s · %s：内容为空" % (label, filename))
                    break
                return obj, label
            except requests.exceptions.SSLError:
                # 个别加速镜像证书链不全，降级重试一次
                continue
            except Exception as e:
                errors.append("%s · %s：%s" % (label, filename, str(e)[:70]))
                break
    return None, ""


def plugins_update_library(budget=100.0):
    """拉取 ComfyUI-Manager 官方插件库，合并写入 models.db。

    写库规则（按用户要求）：
      · nodes 表按【规范化仓库地址】合并 —— 地址已存在则覆盖名称/描述，没有则新增，
        不做简单追加；用户手动添加的条目不会被删。
      · node_map 表（节点类名 → 仓库）是上游快照的派生物，整表重建。
    返回 {ok, source, added, updated, unchanged, total, node_map, errors, elapsed}
    """
    # 延迟导入：_wf_* 仍住在 services/__init__.py（workflows 域还没拆出来）
    from . import _wf_repo_url, _wf_invalidate_pack_map

    started = time.time()
    deadline = started + budget
    errors = []

    node_list, src1 = _fetch_lib_file("custom-node-list.json", errors, deadline)
    ext_map, src2 = _fetch_lib_file("extension-node-map.json", errors, deadline)
    if node_list is None and ext_map is None:
        return {"ok": False, "msg": "所有数据源都不可用，请检查网络后重试",
                "errors": errors, "elapsed": round(time.time() - started, 1)}

    items = []
    if isinstance(node_list, dict):
        for it in (node_list.get("custom_nodes") or []):
            if not isinstance(it, dict):
                continue
            name = it.get("title") or it.get("name") or ""
            url = it.get("reference") or ""
            if not url:
                for u in (it.get("files") or []):
                    if isinstance(u, str) and u:
                        url = u
                        break
            url = _wf_repo_url(url)
            if url:
                items.append((name, url, it.get("description") or ""))

    pairs = []
    if isinstance(ext_map, dict):
        for url, val in ext_map.items():
            nodes = val[0] if isinstance(val, (list, tuple)) and val else []
            if not isinstance(nodes, list):
                continue
            rurl = _wf_repo_url(url)
            if not rurl:
                continue
            for n in nodes:
                if isinstance(n, str) and n:
                    pairs.append((n, rurl))

    from .. import database as _db
    res = {"ok": True, "source": src1 or src2,
           "node_list_source": src1, "node_map_source": src2,
           "fetched": {"plugins": len(items), "node_pairs": len(pairs)},
           "errors": errors, "elapsed": round(time.time() - started, 1)}
    try:
        if items:
            res.update(_db.plugin_upsert_bulk(items))
        if pairs:
            res["node_map_written"] = _db.node_map_replace(pairs)
        res["node_map"] = _db.node_map_stats()
        res["total"] = _db.plugin_count("")
    except Exception as e:
        res.update({"ok": False, "msg": "写入数据库失败：%s" % e})
    _wf_invalidate_pack_map()
    res["elapsed"] = round(time.time() - started, 1)
    return res


def plugins_ensure_row(url, name=""):
    """按规范化仓库地址找插件库条目；没有就补一条，返回 id（失败返回 0）。

    工作流解析给出的仓库来自上游映射表，有极少数（约 0.3%）不在插件库列表里。
    用户真要装它时再补录即可，避免为了这点覆盖率去膨胀整个插件库。
    """
    from .. import database as _db
    url = (url or "").strip()
    if not url:
        return 0
    k = _db._norm_plugin_url(url)
    if not k:
        return 0
    try:
        hit = _db.plugin_ids_by_url().get(k)
    except Exception:
        hit = None
    if hit:
        return hit[0]
    name = (name or "").strip() or k.rstrip("/").split("/")[-1]
    conn = None
    try:
        conn = _db.get_db()
        cur = conn.execute(
            "INSERT INTO nodes (name, description, url) VALUES (?,?,?)",
            (name, "", url))
        conn.commit()
        return cur.lastrowid or 0
    except Exception:
        return 0
    finally:
        if conn is not None:
            conn.close()


def plugins_clone_by_url(url, name=""):
    """按仓库地址克隆插件（工作流解析弹窗的「下载」用）。

    先确保插件库里有这一条（拿到 id），再复用 start_clone ——
    镜像改写/队列/并发上限/进度上报全都不用重写一遍。
    """
    # 延迟导入：start_clone 仍住在 services/__init__.py（clone 域还没拆出来）
    from . import start_clone
    pid = plugins_ensure_row(url, name)
    if not pid:
        return False, "无法登记该插件仓库: " + str(url)[:80], 0
    ok, msg = start_clone(pid)
    # 连 id 一起回给前端：解析结果里没有 id 的那 0.3% 也要能轮询进度
    return ok, msg, pid
