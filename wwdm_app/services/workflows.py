# -*- coding: utf-8 -*-
"""工作流域：工作流文件浏览/增删改 + 缺失插件与缺失模型的解析检测。

自 services/__init__.py 拆出（v5.0 第 6 步 域 7/11），函数体逐字未改。
跨域依赖一律函数内延迟导入（`from . import X`）。

两条解析路径：
  · ComfyUI 在运行 → GET /object_info 拿本机已注册节点（最准）
  · ComfyUI 未运行 → 各插件 .git/config 的 origin URL 反查映射表（覆盖不全，判定偏保守）
"""
import json
import logging
import os
import re
import shutil
import threading
import time

from ..config import settings

log = logging.getLogger("wwdm.services")   # 与 services 包同一个 logger 对象


# ============================================================
# 工作流管理 — 文件浏览
# ============================================================

# 工作流允许的文件扩展名（JSON 及文本类）
_WF_EXTS = {".json", ".txt", ".md", ".png"}


def _workflow_type(name):
    """工作流文件类型：json / text / image / other"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _IMG_EXTS
    ext = os.path.splitext(name)[1].lower()
    if ext == ".json":
        return "json"
    if ext in (".txt", ".md"):
        return "text"
    if ext in _IMG_EXTS:
        return "image"
    return "other"


def workflows_browse(root_dir, rel_path="", page=1, page_size=20,
                     search="", sort="date_desc"):
    """浏览工作流目录：文件夹在前、文件在后，支持分页/搜索/排序。
    与 browse_output_dir 类似，但不过滤非媒体文件（保留全部类型）。
    带 search 时执行跨文件夹模糊搜索。"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path, recursive_search
    if search and search.strip():
        return recursive_search(root_dir, search.strip(), sort, page, page_size, _workflow_type)
    target = _safe_path(root_dir, rel_path)
    if not target:
        return {"ok": False, "msg": "无效路径"}
    if not os.path.isdir(target):
        return {"ok": False, "msg": "目录不存在"}

    # 面包屑
    breadcrumbs = []
    try:
        rel_norm = os.path.relpath(target, root_dir)
    except ValueError:
        rel_norm = ""
    if rel_norm == ".":
        rel_norm = ""
    parts = rel_norm.replace("\\", "/").split("/") if rel_norm else []
    acc = ""
    for p in parts:
        if not p:
            continue
        acc = os.path.join(acc, p) if acc else p
        breadcrumbs.append({"name": p, "path": acc.replace("\\", "/")})

    try:
        entries = os.listdir(target)
    except OSError:
        return {"ok": False, "msg": "无法读取目录"}

    folders_raw = []
    files_raw = []
    for name in entries:
        full = os.path.join(target, name)
        if os.path.isdir(full):
            child_rel = os.path.join(rel_path, name) if rel_path else name
            try:
                st = os.stat(full)
                folders_raw.append({
                    "name": name, "path": child_rel.replace("\\", "/"),
                    "modified": st.st_mtime, "is_dir": True,
                })
            except OSError:
                pass
        elif os.path.isfile(full):
            ft = _workflow_type(name)
            if ft == "other":
                continue
            try:
                st = os.stat(full)
                files_raw.append({
                    "name": name,
                    "path": (os.path.join(rel_path, name).replace("\\", "/") if rel_path else name),
                    "type": ft, "size": st.st_size,
                    "modified": st.st_mtime, "is_dir": False,
                })
            except OSError:
                pass

    # 搜索
    if search:
        q = search.lower()
        folders_raw = [f for f in folders_raw if q in f["name"].lower()]
        files_raw = [f for f in files_raw if q in f["name"].lower()]

    # 排序
    rev = sort.endswith("_desc")
    if sort.startswith("name"):
        folders_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
        files_raw.sort(key=lambda x: x["name"].lower(), reverse=rev)
    elif sort.startswith("date"):
        folders_raw.sort(key=lambda x: x["modified"], reverse=rev)
        files_raw.sort(key=lambda x: x["modified"], reverse=rev)
    else:
        folders_raw.sort(key=lambda x: x["name"].lower())
        files_raw.sort(key=lambda x: x["name"].lower())

    # 分页
    total = len(folders_raw) + len(files_raw)
    ps = max(1, min(100, int(page_size)))
    tp = max(1, (total + ps - 1) // ps) if total else 1
    pg = max(1, min(tp, int(page)))
    start = (pg - 1) * ps
    end = start + ps
    all_items = folders_raw + files_raw
    paged = all_items[start:end]

    return {
        "ok": True,
        "current_path": rel_norm.replace("\\", "/") if rel_norm else "",
        "breadcrumbs": breadcrumbs,
        "parent_path": os.path.dirname(rel_norm).replace("\\", "/") if rel_norm else None,
        "items": paged,
        "page": pg, "page_size": ps, "total": total, "total_pages": tp,
        "search": search, "sort": sort,
    }


def workflows_get(root_dir, rel_path):
    """读取工作流文件内容（文本形式）"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.isfile(t):
        return {"ok": False, "msg": "文件不存在或路径无效"}
    try:
        with open(t, "r", encoding="utf-8") as f:
            content = f.read()
        return {"ok": True, "name": os.path.basename(t), "path": rel_path, "content": content}
    except Exception as e:
        return {"ok": False, "msg": "读取失败: " + str(e)}


def workflows_save(root_dir, rel_path, content):
    """保存工作流文件（新建或覆盖）。返回 {ok, path, msg}。"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    t = _safe_path(root_dir, rel_path)
    if not t:
        return {"ok": False, "msg": "无效路径"}
    os.makedirs(os.path.dirname(t), exist_ok=True)
    try:
        with open(t, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": rel_path.replace("\\", "/"), "msg": "已保存"}
    except Exception as e:
        return {"ok": False, "msg": "保存失败: " + str(e)}


def workflows_new(root_dir, name):
    """在根目录新建空白工作流 JSON 文件"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    safe_name = os.path.basename(name)
    if not safe_name:
        return {"ok": False, "msg": "无效的文件名"}
    if not safe_name.lower().endswith(".json"):
        safe_name += ".json"
    t = _safe_path(root_dir, safe_name)
    if not t:
        return {"ok": False, "msg": "无效的文件名"}
    if os.path.exists(t):
        return {"ok": False, "msg": "文件已存在: " + safe_name}
    try:
        with open(t, "w", encoding="utf-8") as f:
            f.write('{\n  "nodes": [],\n  "links": [],\n  "version": 0.4\n}\n')
        return {"ok": True, "name": safe_name, "path": safe_name, "msg": "已创建"}
    except Exception as e:
        return {"ok": False, "msg": "创建失败: " + str(e)}


def workflows_move_files(root_dir, rel_paths, dest_rel):
    """批量移动工作流文件/文件夹到目标目录"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            shutil.move(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已移动"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def workflows_copy_files(root_dir, rel_paths, dest_rel):
    """批量复制工作流文件/文件夹到目标目录"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    dest = _safe_path(root_dir, dest_rel)
    if not dest:
        return {"ok": False, "msg": "目标目录无效: " + (dest_rel or "(root)")}
    os.makedirs(dest, exist_ok=True)
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        try:
            if os.path.isdir(t):
                d = os.path.join(dest, os.path.basename(t))
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(t, d)
            else:
                shutil.copy2(t, dest)
            results.append({"path": rp, "ok": True, "msg": "已复制"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def workflows_backup(root_dir, backup_dir):
    """将整个工作流目录复制到指定备份目录下的「年月日时分秒-备份」文件夹"""
    if not os.path.isdir(root_dir):
        return {"ok": False, "msg": "工作流目录不存在: " + root_dir}
    if not backup_dir or not backup_dir.strip():
        return {"ok": False, "msg": "未配置备份目录"}
    backup_dir = backup_dir.strip()
    # 防御：备份目录不能位于工作流目录内部（否则 copytree 会无限递归）
    try:
        if os.path.commonpath([os.path.abspath(root_dir), os.path.abspath(backup_dir)]) == os.path.abspath(root_dir):
            return {"ok": False, "msg": "备份目录不能位于工作流目录内部，请改用工作流目录之外的路径"}
    except ValueError:
        pass  # 不同盘符等无法比较的情况，交由后续处理
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建备份目录: " + str(e)}
    # 时间戳：年月日时分秒
    stamp = time.strftime("%Y%m%d%H%M%S")
    dest = os.path.join(backup_dir, stamp + "-备份")
    if os.path.exists(dest):
        # 罕见冲突，追加毫秒
        dest = os.path.join(backup_dir, stamp + "-" + str(int(time.time() * 1000) % 1000) + "-备份")
    try:
        shutil.copytree(root_dir, dest)
        return {"ok": True, "dest": dest, "msg": "备份完成"}
    except Exception as e:
        return {"ok": False, "msg": "备份失败: " + str(e)}


def serve_workflow_file(root_dir, rel_path):
    """安全返回工作流文件绝对路径（供 send_file 下载使用）"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    target = _safe_path(root_dir, rel_path)
    if not target or not os.path.isfile(target):
        return None
    return target




# ==================== 工作流解析：模型 + 插件 ====================
# 复刻 ComfyUI-Manager 的「缺失插件检测」思路：
#     工作流里的节点类名  −  本机已注册的节点类名  =  缺失节点
#     缺失节点再经 extension-node-map.json 反查 → 该装哪个插件仓库
# 「本机已注册节点」有两个来源：
#     ① ComfyUI 在运行 → GET /object_info（带 python_module，最准，与 Manager 前端同源）
#     ② ComfyUI 未运行 → 各插件 .git/config 的 origin URL 去映射表反查（覆盖不全，判定偏保守）
# 改这里要注意：_wf_object_info 的端口取自设置里的 comfyui_port，必须与 wwdmui 启动的那份一致。

_WF_BUILTIN_MODULE_ROOTS = ("nodes", "comfy_extras", "comfy_api_nodes", "comfy_api")
_WF_CORE_REPO = "https://github.com/comfyanonymous/ComfyUI"
# 非插件类节点（纯前端/占位），不参与插件判定
_WF_SKIP_NODE_TYPES = ("Note", "MarkdownNote", "Reroute", "PrimitiveNode",
                       "ReroutePrimitive", "Anything Everywhere")

_wf_lock = threading.Lock()
_wf_objinfo_cache = {"ts": 0.0, "port": "", "map": None}
_wf_packmap_cache = {"ts": 0.0, "root": "", "rev": None, "titles": None, "byurl": None}
_wf_modelidx_cache = {"ts": 0.0, "key": "", "index": None}


def _wf_port():
    """ComfyUI 服务端口（设置里没填就用 8188）。允许写成 host:port。"""
    try:
        p = (settings.get("comfyui_port") or "").strip()
    except Exception:
        p = ""
    if not p:
        return "8188"
    if ":" in p:
        p = p.rsplit(":", 1)[1]
    return p.strip() or "8188"


def _wf_url_key(u):
    """仓库 URL 归一化。统一走 database._norm_plugin_url ——
    它会顺带剥掉 gh-proxy / gitclone 这类加速前缀，而 .git/config 里的 origin
    经常带前缀，两边各写一套归一化必然对不上。"""
    from .. import database as _db
    return _db._norm_plugin_url(u)

def _wf_object_info(ttl=30.0):
    """读运行中 ComfyUI 的节点注册表。返回 (map{类名: python_module}, online)。"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _NO_PROXY
    port = _wf_port()
    now = time.time()
    with _wf_lock:
        c = _wf_objinfo_cache
        if c["map"] is not None and c["port"] == port and now - c["ts"] < ttl:
            return c["map"], True
    mp = {}
    ok = False
    try:
        import requests
        r = requests.get("http://127.0.0.1:" + port + "/object_info",
                         timeout=(3, 10), proxies=_NO_PROXY)
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, dict):
                for k, v in raw.items():
                    mp[k] = (v.get("python_module") or "") if isinstance(v, dict) else ""
                ok = True
    except Exception:
        ok = False
    if not ok:
        return {}, False
    with _wf_lock:
        _wf_objinfo_cache.update({"ts": now, "port": port, "map": mp})
    return mp, True


def _wf_pack_map(ttl=300.0):
    """节点类名 ↔ 插件仓库 映射，直接读**内置的** models.db（node_map + nodes 表）。

    数据随包分发，不读 ComfyUI-Manager 的目录；过期了在插件管理页点「更新插件库」刷新。
    返回 (rev, titles, byurl)：
        rev    {节点类名: [仓库URL, ...]}      原始 URL，界面可直接当链接
        titles {url_key: 插件名}
        byurl  {url_key: set(节点类名)}
    """
    now = time.time()
    with _wf_lock:
        c = _wf_packmap_cache
        if c["rev"] is not None and now - c["ts"] < ttl:
            return c["rev"], c["titles"], c["byurl"]
    from .. import database as _db
    try:
        rev, byurl = _db.node_map_load()
        titles = _db.plugin_url_titles()
    except Exception:
        rev, byurl, titles = {}, {}, {}
    with _wf_lock:
        _wf_packmap_cache.update({"ts": now, "root": "", "rev": rev,
                                  "titles": titles, "byurl": byurl})
    return rev, titles, byurl


def _wf_invalidate_pack_map():
    """更新插件库后强制下一次重新读表。"""
    with _wf_lock:
        _wf_packmap_cache.update({"ts": 0.0, "root": "", "rev": None,
                                  "titles": None, "byurl": None})


def _wf_installed_keys(byurl):
    """已装插件对应的规范化 URL 集合（离线判定用）。

    两条路：① `.git/config` 里的 origin URL；② 目录名与映射表里仓库 basename 相同。
    ② 覆盖那些没有 .git 的包（本机 168 个插件目录里只有 107 个带 .git）。
    返回 (keys:set, pack_urls:dict{目录名: origin URL})
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import plugins_root
    pack_urls = _wf_installed_pack_urls()
    keys = set()
    for u in pack_urls.values():
        k = _wf_url_key(u)
        if k:
            keys.add(k)
    base2key = {}
    for k in byurl:
        b = k.rstrip("/").split("/")[-1]
        if b:
            base2key.setdefault(b, k)
    try:
        names = os.listdir(plugins_root())
    except Exception:
        names = []
    for nm in names:
        k = _wf_url_key(pack_urls.get(nm, "")) or base2key.get(nm.lower(), "")
        if k:
            keys.add(k)
    return keys, pack_urls

def _wf_installed_pack_urls():
    """扫 custom_nodes 各插件的 .git/config 取 origin URL → {包目录名: URL}。"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import plugins_root
    out = {}
    root = plugins_root()
    try:
        names = os.listdir(root)
    except Exception:
        return out
    for nm in names:
        p = os.path.join(root, nm)
        if not os.path.isdir(p):
            continue
        cfg = os.path.join(p, ".git", "config")
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8", errors="replace") as f:
                s = f.read()
        except Exception:
            continue
        m = re.search(r"url\s*=\s*(\S+)", s)
        if m:
            out[nm] = m.group(1)
    return out


def _wf_model_index(ttl=60.0):
    """本机模型文件名索引 {小写文件名: 相对路径}，用于判断工作流引用的模型在不在。"""
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import models_root
    root = models_root()
    now = time.time()
    try:
        key = str(int(os.stat(root).st_mtime_ns))
    except Exception:
        key = ""
    with _wf_lock:
        c = _wf_modelidx_cache
        if c["index"] is not None and c["key"] == key and now - c["ts"] < ttl:
            return c["index"]
    idx = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                idx.setdefault(fn.lower(), rel)
    except Exception:
        pass
    with _wf_lock:
        _wf_modelidx_cache.update({"ts": now, "key": key, "index": idx})
    return idx


def _wf_parse_api_nodes(data):
    """兼容 ComfyUI「API 格式」工作流（{id: {class_type, inputs}}）。

    转成与 UI 格式等价的 node 列表；不是 API 格式则返回 None。
    """
    if not isinstance(data, dict) or not data:
        return None
    out = []
    for v in data.values():
        if not isinstance(v, dict) or "class_type" not in v:
            return None
        ntype = str(v.get("class_type") or "")
        title = ""
        meta = v.get("_meta")
        if isinstance(meta, dict):
            title = str(meta.get("title") or "")
        vals = []
        inputs = v.get("inputs")
        if isinstance(inputs, dict):
            for iv in inputs.values():
                if isinstance(iv, str):
                    vals.append(iv)
                elif isinstance(iv, list):
                    for x in iv:
                        if isinstance(x, str):
                            vals.append(x)
        out.append({"type": ntype, "title": title, "widgets_values": vals})
    return out


def _wf_classify(ntype, info, online, rev, titles, byurl, installed_keys, pack_urls):
    """判定单个节点类型：内置 / 已装插件 / 缺失 / 未知。"""
    core_key = _wf_url_key(_WF_CORE_REPO)
    all_repos = rev.get(ntype) or []
    repos = [u for u in all_repos if _wf_url_key(u) != core_key]
    repo = repos[0] if repos else ""
    title = titles.get(_wf_url_key(repo), "") if repo else ""

    if online:
        if ntype not in info:
            # ComfyUI 在跑但没注册这个类名 → 确实缺
            return {"type": ntype, "state": "missing" if repo else "unknown",
                    "pack": "", "repo": repo, "title": title}
        mod = info.get(ntype) or ""
        if mod.startswith("custom_nodes."):
            pack = mod.split(".")[1] if "." in mod else mod
            # 已装插件的仓库只认 .git/config 里读到的真实地址，不用映射表猜（会张冠李戴）
            u = pack_urls.get(pack, "")
            return {"type": ntype, "state": "installed", "pack": pack, "repo": u,
                    "title": titles.get(_wf_url_key(u), "") if u else ""}
        return {"type": ntype, "state": "builtin", "pack": "", "repo": "", "title": ""}

    # 离线：靠「已装插件的 URL 集合」与映射表比对
    for u in all_repos:
        k = _wf_url_key(u)
        if k and k in installed_keys and k != core_key:
            return {"type": ntype, "state": "installed",
                    "pack": u.rstrip("/").split("/")[-1],
                    "repo": u, "title": titles.get(k, "")}
    if core_key in [_wf_url_key(u) for u in all_repos]:
        return {"type": ntype, "state": "builtin", "pack": "", "repo": "", "title": ""}
    if repo:
        return {"type": ntype, "state": "missing", "pack": "", "repo": repo, "title": title}
    return {"type": ntype, "state": "unknown", "pack": "", "repo": "", "title": ""}

def _wf_repo_url(u):
    """把「指向仓库里某个文件」的地址收敛成可 git clone 的仓库根地址。

    上游 extension-node-map.json 里混着 raw.githubusercontent.com/<o>/<r>/.../x.py
    这类单文件地址，还有个别 gist。前者能还原成仓库，后者根本不是仓库 ——
    直接丢掉，免得界面把 .py 地址当「仓库」给用户去 clone。
    """
    u = (u or "").strip().rstrip("/")
    if not u:
        return ""
    m = re.match(r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/", u, re.I)
    if m:
        return "https://github.com/" + m.group(1) + "/" + m.group(2)
    if re.search(r"gist\.", u, re.I):
        return ""
    p = re.match(r"(https?://[^/]+)/([^/]+)/([^/]+)", u, re.I)
    if not p:
        return ""
    host, owner, repo = p.group(1), p.group(2), p.group(3)
    if "." not in host.split("//", 1)[-1]:
        return ""
    repo = re.sub(r"\.git$", "", repo, flags=re.I)
    if re.search(r"\.(py|json|ya?ml|txt|md|csv|toml|ini|safetensors)$", repo, re.I):
        return ""
    return host + "/" + owner + "/" + repo


def _wf_bind_model_lib(found):
    """给解析出的模型补 exists / at / id 三个字段。

    返回 (缺失数, 其中模型库里查得到下载地址的数量)。

    * exists / at —— 来自本机 models 目录的文件名索引；
    * id —— 来自 models.db（只收录带 download_url 的记录），界面上的「下载」按钮
      复用它走既有的按 id 下载流程（队列、并发上限、镜像改写、进度都现成）。

    JSON 工作流与非 JSON 兜底（.txt/.md/.png）都走这里，两条路径口径一致。
    """
    from .. import database as _db

    midx = _wf_model_index()
    missing = 0
    for m in found:
        norm = (m.get("file") or "").replace("\\", "/").strip().lower()
        base = norm.split("/")[-1]
        at = midx.get(norm) or midx.get(base) or ""
        m["exists"] = bool(at)
        m["at"] = at
        m["id"] = 0          # 0 = 模型库里没有 -> 前端不给下载按钮，显示「库中无」
        if not at:
            missing += 1

    try:
        idmap = _db.model_ids_by_file()
    except Exception as e:
        log.warning("[解析] 读取模型库索引失败: %s", e)
        idmap = {}
    in_lib = 0
    for m in found:
        if m.get("exists"):
            continue
        key = (m.get("file") or "").replace("\\", "/").strip().lower().split("/")[-1]
        hit = idmap.get(key)
        if hit:
            m["id"] = hit[0]
            in_lib += 1
    return missing, in_lib


def workflows_analyze(root_dir, rel_path):
    """解析工作流：列出所需【模型】与所需【插件】，并判断本机是否已有。

    插件部分复刻 ComfyUI-Manager 的缺失检测：
        工作流节点类名 − 本机已注册节点类名 = 缺失节点 → 映射表反查所属插件仓库
    返回 {ok, name, path, type, models[], plugins[], missing_*}
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.isfile(t):
        return {"ok": False, "msg": "文件不存在或路径无效"}
    try:
        with open(t, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"ok": False, "msg": "读取失败: " + str(e)}

    # 尝试解析 JSON
    try:
        data = json.loads(content)
    except Exception:
        # 非 JSON 文件（.txt/.md/.png）：只能正则兜底捞模型名
        res = _workflows_analyze_text(content, rel_path)
        # 非 JSON 也要判存在性、也要能一键下载（模型库里有地址就行），
        # 否则前端会把每个模型都显示成「未找到」、徽章还会说「模型已齐全」。
        _mm, _mml = _wf_bind_model_lib(res.get("models") or [])
        res.update({"plugins": [], "plugin_count": 0, "missing_plugin_count": 0,
                    "missing_model_count": _mm, "missing_model_in_lib": _mml,
                    "builtin_node_count": 0, "unknown_nodes": [],
                    "comfyui_online": False, "source": "text"})
        return res

    nodes = data.get("nodes") if isinstance(data, dict) else None
    if isinstance(nodes, list):
        fmt = "ui"
    else:
        nodes = _wf_parse_api_nodes(data)
        fmt = "api"
    if nodes is None:
        return {"ok": False, "msg": "工作流 JSON 结构无效（既没有 nodes 数组，也不是 API 格式）"}

    # 常见模型扩展名（长的优先匹配，如 .safetensors 在 .safetensors.bak 之前）
    model_exts = (".safetensors", ".pth", ".pt", ".ckpt", ".gguf", ".onnx",
                  ".bin", ".sft", ".pt2", ".vae.pt", ".vae.safetensors",
                  ".safetensors.bak", ".pth.tar", ".tar", ".zip", ".json")
    model_exts = tuple(sorted(model_exts, key=len, reverse=True))

    found = []
    seen = set()
    node_types = []
    seen_types = set()

    def add_model(fname, node_name, node_type):
        if not fname or not isinstance(fname, str):
            return
        fname = fname.strip()
        if not fname:
            return
        low = fname.lower()
        if not any(low.endswith(e) for e in model_exts):
            return
        if fname in seen:
            return
        seen.add(fname)
        found.append({
            "file": fname,
            "ext": os.path.splitext(fname)[1].lower(),
            "node": node_name or "",
            "node_type": node_type or "",
        })

    def add_type(ntype):
        if not ntype or not isinstance(ntype, str):
            return
        if ntype in seen_types or ntype in _WF_SKIP_NODE_TYPES:
            return
        seen_types.add(ntype)
        node_types.append(ntype)

    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type", "")
        nname = node.get("title", "") or node.get("type", "")
        add_type(ntype)
        # widgets_values：常含 ckpt_name / lora_name 等模型选择值
        wv = node.get("widgets_values")
        if isinstance(wv, list):
            for v in wv:
                if isinstance(v, str):
                    add_model(v, nname, ntype)
                elif isinstance(v, list):
                    for sv in v:
                        if isinstance(sv, str):
                            add_model(sv, nname, ntype)
        # inputs 中的 widget 默认值（部分节点结构）
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            w = inp.get("widget")
            if isinstance(w, dict) and w.get("name") and inp.get("value") is not None:
                v = inp["value"]
                if isinstance(v, str):
                    add_model(v, nname, ntype)
            if inp.get("value") and isinstance(inp["value"], str):
                add_model(inp["value"], nname, ntype)

    # ---- 模型：对照本机 models 目录标出缺失，并绑上模型库里的下载入口 ----
    missing_models, missing_model_in_lib = _wf_bind_model_lib(found)

    # ---- 插件：节点类名差集 + 映射表反查 ----
    from .. import database as _db

    info, online = _wf_object_info()
    rev, titles, byurl = _wf_pack_map()
    installed_keys, pack_urls = _wf_installed_keys(byurl)

    plugins_map = {}
    miss_map = {}
    builtin_nodes = []
    unknown_nodes = []
    for ntype in node_types:
        r = _wf_classify(ntype, info, online, rev, titles, byurl, installed_keys, pack_urls)
        st = r["state"]
        if st == "builtin":
            builtin_nodes.append(ntype)
        elif st == "installed":
            key = r["pack"] or r["type"]
            b = plugins_map.setdefault(key, {"pack": r["pack"] or r["type"], "installed": True,
                                             "repo": r["repo"], "title": r["title"], "nodes": []})
            b["nodes"].append(r["type"])
            if r["repo"] and not b["repo"]:
                b["repo"] = r["repo"]
            if r["title"] and not b["title"]:
                b["title"] = r["title"]
        elif st == "missing":
            key = r["pack"] or (r["repo"].rstrip("/").split("/")[-1] if r["repo"] else r["type"])
            b = miss_map.setdefault(key, {"pack": key, "installed": False,
                                          "repo": r["repo"], "title": r["title"], "nodes": []})
            b["nodes"].append(r["type"])
            if r["title"] and not b["title"]:
                b["title"] = r["title"]
        else:
            unknown_nodes.append(ntype)

    # 缺失的排前面（用户最需要看到），同组内按节点数降序
    plugins = sorted(list(miss_map.values()) + list(plugins_map.values()),
                     key=lambda x: (x["installed"], -len(x["nodes"]), x["pack"].lower()))

    # 绑上插件库里的条目 id —— 界面上的「下载」按钮直接复用它，
    # 走既有的按 id 克隆流程（自带队列、并发上限、GitHub 镜像改写与进度）。
    try:
        _idmap = _db.plugin_ids_by_url()
    except Exception:
        _idmap = {}
    for p in plugins:
        hit = _idmap.get(_wf_url_key(p.get("repo") or ""))
        p["id"] = hit[0] if hit else 0
        if not p.get("title") and hit and hit[1]:
            p["title"] = hit[1]

    return {
        "ok": True,
        "name": os.path.basename(t),
        "path": rel_path,
        "type": fmt,
        "source": "comfyui" if online else "local",
        "comfyui_online": online,
        "models": found,
        "count": len(found),
        "model_count": len(found),
        "missing_model_count": missing_models,
        "missing_model_in_lib": missing_model_in_lib,   # 其中能在模型库里找到下载地址的
        "plugins": plugins,
        "plugin_count": len(plugins),
        "missing_plugin_count": len(miss_map),
        "builtin_node_count": len(builtin_nodes),
        "unknown_nodes": unknown_nodes,
        # 内置插件库规模（models.db 的 node_map 表）：为空说明从没更新过库
        "lib_nodes": len(rev),
        "lib_repos": len(byurl),
    }


def _workflows_analyze_text(content, rel_path):
    """非 JSON 工作流（.txt/.md/.png）用正则提取模型文件名"""
    model_exts = (".safetensors", ".pth", ".pt", ".ckpt", ".gguf", ".onnx",
                  ".bin", ".sft", ".pt2", ".vae.pt", ".vae.safetensors")
    model_exts = tuple(sorted(model_exts, key=len, reverse=True))
    ext_alt = "|".join(re.escape(e.lstrip(".")) for e in model_exts)
    # 文件名正则：允许中文、字母、数字、_ - . 空格、路径分隔符
    pat = re.compile(
        r"[A-Za-z0-9_\-\u4e00-\u9fff /\\.]+\.(?:" + ext_alt + r")\b",
        re.I,
    )
    found = []
    seen = set()
    for m in pat.finditer(content):
        fname = m.group(0).strip()
        base = fname.replace("\\", "/").split("/")[-1].strip()
        if not base or base in seen:
            continue
        seen.add(base)
        found.append({"file": base, "ext": os.path.splitext(base)[1].lower(),
                      "node": "", "node_type": ""})
    return {
        "ok": True,
        "name": os.path.basename(rel_path),
        "path": rel_path,
        "models": found,
        "count": len(found),
        "type": "text",
    }
