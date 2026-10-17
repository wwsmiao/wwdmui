# -*- coding: utf-8 -*-
"""本地文件域：ComfyUI models / custom_nodes 目录的浏览、统计、增删改、移动、打开。

自 services/__init__.py 拆出（v5.0 第 6 步 域 6/11），函数体逐字未改。
跨域依赖一律函数内延迟导入（`from . import X`）。

这里也是「路径根」的家：`models_root()` / `plugins_root()` 与本地浏览共用同一套
作用域概念（LOCAL_SCOPES / _local_scope / _local_root）。它们被 workflows 域引用，
由 services/__init__.py 末尾的再导出并回包命名空间。
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time

from ..config import IS_WINDOWS, get_comfyui_dir


# ============================================================
# 本地模型文件管理（ComfyUI models 目录）
#   与「数据库模型」互不影响：这里直接读写磁盘上的模型文件
# ============================================================
MODEL_LOCAL_EXTS = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf", ".sft",
    ".pkl", ".npz", ".npy", ".h5", ".pb", ".engine", ".trt", ".mlmodel",
    ".model", ".msgpack", ".ggml",
}

# 本地浏览的作用域：模型目录（models）/ 插件目录（custom_nodes）
LOCAL_SCOPES = {"models": "models", "plugins": "custom_nodes"}


def models_root():
    """ComfyUI 模型根目录：<comfyui_dir>/models"""
    return os.path.join(get_comfyui_dir(), "models")


def plugins_root():
    """ComfyUI 插件根目录：<comfyui_dir>/custom_nodes"""
    return os.path.join(get_comfyui_dir(), "custom_nodes")


def _local_scope(scope):
    return scope if scope in LOCAL_SCOPES else "models"


def _local_root(scope="models"):
    return models_root() if _local_scope(scope) == "models" else plugins_root()


def _local_root_name(scope="models"):
    return LOCAL_SCOPES[_local_scope(scope)]


def _local_rel(rel):
    """规范化前端传来的相对路径；含 ".." 视为非法，返回 None"""
    r = (rel or "").replace("\\", "/").strip()
    parts = [p for p in r.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _local_abs(rel, scope="models"):
    """相对路径 -> 绝对路径（并强制校验仍在根目录内）；非法返回 None

    校验统一交给 ``_safe_path``，不再自己复一份 commonpath —— 两处实现分头演进
    正是之前「这个入口拦得住、那个入口拦不住」的来源。
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _safe_path
    r = _local_rel(rel)
    if r is None:
        return None
    return _safe_path(_local_root(scope), r)


def _local_crumbs(rel, scope="models"):
    crumbs = [{"name": _local_root_name(scope), "rel": ""}]
    acc = []
    for part in (rel or "").split("/"):
        if not part:
            continue
        acc.append(part)
        crumbs.append({"name": part, "rel": "/".join(acc)})
    return crumbs


# ---- 目录递归统计（本地模式状态栏 + 文件夹卡片共用） ----
_dir_stats_cache = {}
_dir_stats_lock = threading.Lock()


def invalidate_local_usage():
    """本地文件增删改后调用，立即失效目录统计缓存"""
    with _dir_stats_lock:
        _dir_stats_cache.clear()


def _scan_tree(cur_abs, kids):
    """用 scandir 递归统计目录：返回 (子目录总数, 文件数, 总占用)。
    Windows 上 entry.stat() 取自目录枚举缓存，比 os.walk + os.path.getsize 快数倍。
    kids 为一级子项 => 统计桶（只累计其后代，不含自身）。"""
    dirs = 0
    files = 0
    size = 0
    stack = [(cur_abs, None)]
    while stack:
        cur, top = stack.pop()
        try:
            it = os.scandir(cur)
        except Exception:
            continue
        with it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except Exception:
                    continue
                if is_dir:
                    dirs += 1
                    if top is not None:
                        top["dirs"] += 1
                        stack.append((e.path, top))
                    else:
                        try:
                            link = e.is_symlink()
                        except Exception:
                            link = False
                        if not link:
                            stack.append((e.path, kids.get(e.name)))
                else:
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue
                        sz = e.stat(follow_symlinks=False).st_size
                    except Exception:
                        sz = 0
                    files += 1
                    size += sz
                    if top is not None:
                        top["files"] += 1
                        top["size"] += sz
    return dirs, files, size


def local_dir_stats(rel="", scope="models", ttl=10.0):
    """递归统计根目录下 rel：直接子文件夹数 / 全部子目录数 / 文件数 / 总占用，
    并给出每个一级子项的同口径（递归）统计，供文件夹卡片显示。
    一次 scandir 递归拿到全部数据；结果带 TTL 缓存，缓存键含本目录 mtime，
    外部直接改盘后会自动失效，通过本应用增删改则由 invalidate_local_usage() 立即清空。"""
    sc = _local_scope(scope)
    root = _local_root(sc)
    rn = _local_root_name(sc)
    if not os.path.isdir(root):
        return {"ok": False, "msg": "%s 目录不存在：%s" % (rn, root)}
    r = _local_rel(rel)
    if r is None:
        return {"ok": False, "msg": "非法路径"}
    cur = _local_abs(r, sc)
    if not cur or not os.path.isdir(cur):
        return {"ok": False, "msg": "目录不存在"}
    # 缓存键带上当前目录自身的 mtime：用资源管理器 / ComfyUI 下载等外部方式
    # 往这一层增删文件后，父目录 mtime 变化 => 缓存自动失效，不必干等 TTL；
    # 通过本应用增删改的路径另有 invalidate_local_usage() 立即清空兜底。
    try:
        dir_mt = os.stat(cur).st_mtime_ns
    except Exception:
        dir_mt = 0
    key = (sc, r, dir_mt)
    now = time.time()
    with _dir_stats_lock:
        c = _dir_stats_cache.get(key)
        if c and (now - c[0]) < ttl:
            return c[1]
    kids = {}
    try:
        # scandir 的 is_dir() 取自目录枚举缓存（Windows 上不额外 stat），
        # 原来是 listdir + 逐项 isdir，等于这一层白付一轮 stat
        with os.scandir(cur) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        kids[e.name] = {"size": 0, "files": 0, "dirs": 0}
                except Exception:
                    continue
    except Exception as e:
        return {"ok": False, "msg": "统计失败：%s" % e}
    try:
        dirs, files, size = _scan_tree(cur, kids)
    except Exception as e:
        return {"ok": False, "msg": "统计失败：%s" % e}
    data = {"ok": True, "rel": r, "scope": sc, "subs": len(kids), "dirs": dirs,
            "files": files, "size": size, "kids": kids, "ts": int(now)}
    with _dir_stats_lock:
        _dir_stats_cache[key] = (now, data)
        if len(_dir_stats_cache) > 64:
            old = sorted(_dir_stats_cache, key=lambda k: _dir_stats_cache[k][0])[:16]
            for k in old:
                _dir_stats_cache.pop(k, None)
    return data


def local_models_list(rel="", sort="name_asc", scope="models", with_stats=False):
    """列出 <root>/<rel> 下的子文件夹与文件（只列一层，保证速度）"""
    sc = _local_scope(scope)
    root = _local_root(sc)
    rn = _local_root_name(sc)
    if not os.path.isdir(root):
        return {"ok": False, "msg": "%s 目录不存在：%s" % (rn, root)}
    r = _local_rel(rel)
    if r is None:
        return {"ok": False, "msg": "非法路径"}
    cur = _local_abs(r, sc)
    if not cur or not os.path.isdir(cur):
        return {"ok": False, "msg": "目录不存在"}
    try:
        names = os.listdir(cur)
    except Exception as e:
        return {"ok": False, "msg": "无法读取目录：%s" % e}
    folders, files = [], []
    for name in names:
        fp = os.path.join(cur, name)
        child = (r + "/" + name) if r else name
        try:
            if os.path.isdir(fp):
                try:
                    mt = int(os.stat(fp).st_mtime)
                except Exception:
                    mt = 0
                try:
                    is_git = os.path.isdir(os.path.join(fp, ".git"))
                except Exception:
                    is_git = False
                # 数字（递归口径）由 /api/models/local/stats 异步补齐，列表本身不做遍历
                folders.append({"name": name, "rel": child, "files": None,
                                "dirs": None, "size": None, "mtime": mt,
                                "git": is_git})
            else:
                st = os.stat(fp)
                ext = os.path.splitext(name)[1].lower()
                files.append({"name": name, "rel": child, "size": st.st_size,
                              "mtime": int(st.st_mtime), "ext": ext,
                              "is_model": ext in MODEL_LOCAL_EXTS})
        except Exception:
            continue
    du = None
    try:
        du = shutil.disk_usage(cur)
    except Exception:
        pass
    parent = None
    if r:
        parent = r.rsplit("/", 1)[0] if "/" in r else ""
    # 文件夹卡片的数字一律用递归口径（含多层子目录），和状态栏统计口径一致；
    # 默认交给 /api/models/local/stats 异步补齐，with_stats=True 时同步算（供脚本/旧调用）
    stats = local_dir_stats(r, sc) if with_stats else None
    if stats and stats.get("ok"):
        kids = stats.get("kids") or {}
        for f in folders:
            b = kids.get(f["name"])
            if b:
                f["size"] = b["size"]
                f["files"] = b["files"]
                f["dirs"] = b["dirs"]
                f["rec"] = True
    # 文件与文件夹用同一套排序键（名称 A-Z/Z-A，修改时间 新→旧/旧→新）
    rev = sort.endswith("_desc")
    if sort.startswith("size"):
        files.sort(key=lambda x: x["size"] or 0, reverse=rev)
        folders.sort(key=lambda x: x["size"] or 0, reverse=rev)
    elif sort.startswith("mtime"):
        files.sort(key=lambda x: x["mtime"], reverse=rev)
        folders.sort(key=lambda x: x["mtime"], reverse=rev)
    else:
        files.sort(key=lambda x: x["name"].lower(), reverse=rev)
        folders.sort(key=lambda x: x["name"].lower(), reverse=rev)
    return {
        "ok": True, "root": root, "root_name": rn, "scope": sc,
        "rel": r, "abs": cur, "parent": parent,
        "crumbs": _local_crumbs(r, sc),
        "folders": folders, "files": files,
        "file_total": len(files), "size_total": sum(f["size"] for f in files),
        "disk_total": du.total if du else 0, "disk_free": du.free if du else 0,
        "dir_total": len(folders),
        "stats": stats if (stats and stats.get("ok")) else None,
    }


def local_models_search(q, rel="", limit=500, max_depth=5, sort="size_desc",
                        scope="models", include_dirs=False):
    """在 <root>/<rel> 下递归搜索（有数量上限，返回 truncated 标记）；
    结果排序沿用界面上的排序下拉（名称 / 修改时间，正反序）。
    include_dirs=True 时同时匹配子文件夹名（插件目录场景用）。"""
    sc = _local_scope(scope)
    root = _local_root(sc)
    rn = _local_root_name(sc)
    if not os.path.isdir(root):
        return {"ok": False, "msg": "%s 目录不存在：%s" % (rn, root)}
    q = (q or "").strip().lower()
    if not q:
        return {"ok": True, "items": [], "truncated": False, "q": ""}
    base = _local_abs(rel, sc)
    if not base or not os.path.isdir(base):
        return {"ok": False, "msg": "目录不存在"}
    items, truncated = [], False
    for cur, dirs, fns in os.walk(base):
        depth = 0 if cur == base else os.path.relpath(cur, base).count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        if include_dirs and cur != base:
            bn = os.path.basename(cur)
            if q in bn.lower() and not bn.startswith(".") and bn != "__pycache__":
                relp = os.path.relpath(cur, root).replace(os.sep, "/")
                try:
                    mt = int(os.stat(cur).st_mtime)
                except Exception:
                    mt = 0
                items.append({"name": bn, "rel": relp, "size": 0, "mtime": mt,
                              "ext": "", "is_dir": True, "is_model": False,
                              "dir": os.path.dirname(relp)})
                if len(items) >= limit:
                    truncated = True
                    break
        if truncated:
            break
        for fn in fns:
            if q not in fn.lower():
                continue
            fp = os.path.join(cur, fn)
            relp = os.path.relpath(fp, root).replace(os.sep, "/")
            try:
                st = os.stat(fp)
                sz, mt = st.st_size, int(st.st_mtime)
            except Exception:
                sz, mt = 0, 0
            ext = os.path.splitext(fn)[1].lower()
            items.append({"name": fn, "rel": relp, "size": sz, "mtime": mt,
                          "ext": ext, "is_dir": False,
                          "is_model": ext in MODEL_LOCAL_EXTS,
                          "dir": os.path.dirname(relp)})
            if len(items) >= limit:
                truncated = True
                break
        if truncated:
            break
    rev = sort.endswith("_desc")
    if sort.startswith("mtime"):
        items.sort(key=lambda x: x["mtime"], reverse=rev)
    elif sort.startswith("name"):
        items.sort(key=lambda x: x["name"].lower(), reverse=rev)
    else:
        items.sort(key=lambda x: x["size"], reverse=rev)
    return {"ok": True, "items": items, "truncated": truncated, "q": q,
            "root_name": rn, "scope": sc}


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def _ps_run(script, timeout=60):
    """执行一段 PowerShell 脚本并返回 CompletedProcess。

    ⚠️ 必须带 timeout。run.py 是 threaded=True：PowerShell 若卡住（等交互输入、
    被安全软件拦截、目标文件被占用等），没有超时会**永久占住一个请求线程**，
    线程池耗尽后整个管理端失去响应。超时抛 subprocess.TimeoutExpired，由调用方处理。
    """
    cf = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=cf,
        timeout=timeout)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def _shfo_recycle(paths):
    """Windows 原生 SHFileOperationW 移入回收站：不起 PowerShell，快得多。
    实测本机即使成功也可能返回非 0，所以调用方一律以「磁盘上是否还在」判定成败。"""
    try:
        buf = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
        op = _SHFILEOPSTRUCTW()
        op.wFunc = 3                                          # FO_DELETE
        op.pFrom = ctypes.c_wchar_p(buf)
        op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x0200  # ALLOWUNDO|NOCONFIRMATION|SILENT|NOERRORUI|NOCONFIRMMKDIR
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return True
    except Exception:
        return False


def _ps_recycle_stage2(paths):
    """Microsoft.VisualBasic 兜底（会把子项拆成多条记录，但更通用）"""
    arr = ",".join(_ps_quote(p) for p in paths)
    return (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        "$ErrorActionPreference='Continue';"
        "$ProgressPreference='SilentlyContinue';"
        "$ps=@(" + arr + ");"
        "foreach($p in $ps){"
        "try{"
        "if(Test-Path -LiteralPath $p -PathType Container){"
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p,'OnlyErrorDialogs','SendToRecycleBin')"
        "}elseif(Test-Path -LiteralPath $p){"
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin')"
        "}}catch{Write-Output ('ERR::'+$p+'::'+$_.Exception.Message)}}"
    )


def _send_to_trash(paths):
    """把文件/文件夹移入系统回收站（可恢复），不做永久删除。
    成败一律以「磁盘上是否还在」判定 —— 回收站 API 会在删除成功后仍抛异常。"""
    if not paths:
        return True, ""
    if IS_WINDOWS:
        ok_all, msgs = True, []
        for i in range(0, len(paths), 20):
            chunk = paths[i:i + 20]
            errs = {}
            todo = [p for p in chunk if os.path.exists(p)]
            if todo:
                _shfo_recycle(todo)
            todo = [p for p in chunk if os.path.exists(p)]
            if todo:
                # 兜底：原生 API 没删掉的交给 PowerShell（长路径/只读等场景）
                try:
                    r = _ps_run(_ps_recycle_stage2(todo))
                except subprocess.TimeoutExpired:
                    return False, ("调用系统回收站超时（超过 60 秒），"
                                   "可能有文件被占用或正在上传，请稍后重试")
                except Exception as e:
                    return False, "调用系统回收站失败：%s" % e
                out = r.stdout or ""
                for line in out.splitlines():
                    if line.startswith("ERR::"):
                        parts = line[5:].split("::", 2)
                        if len(parts) >= 2:
                            errs[parts[0]] = parts[-1]
            left = [p for p in chunk if os.path.exists(p)]
            if left:
                ok_all = False
                for p in left:
                    msgs.append("%s — %s" % (os.path.basename(p), errs.get(p, "未能移入回收站")))
        return ok_all, "；".join(msgs[:5])
    try:
        for p in paths:
            if sys.platform == "darwin":
                patch = 'tell application "Finder" to delete POSIX file ' + \
                        json.dumps(p, ensure_ascii=False)
                subprocess.run(["osascript", "-e", patch],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["gio", "trash", p],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    except Exception as e:
        return False, "移入回收站失败：%s" % e


def local_models_delete(rels, permanent=False, scope="models"):
    """删除选中的文件/文件夹：默认移入回收站（可恢复）"""
    invalidate_local_usage()
    sc = _local_scope(scope)
    rn = _local_root_name(sc)
    root = os.path.abspath(_local_root(sc))
    if isinstance(rels, str):
        rels = [rels]
    if not rels:
        return {"ok": False, "msg": "未选择任何文件"}
    targets, bad = [], []
    for rel in rels:
        a = _local_abs(rel, sc)
        if not a:
            bad.append(str(rel))
            continue
        a = os.path.abspath(a)
        if a == root:
            bad.append("%s 根目录" % rn)
            continue
        if not os.path.exists(a):
            bad.append(os.path.basename(a) + " (不存在)")
            continue
        targets.append(a)
    if not targets:
        return {"ok": False, "msg": "没有可删除的目标" + ("（%s）" % "；".join(bad[:3]) if bad else "")}
    targets = sorted(set(targets), key=len)
    final = []
    for t in targets:
        if any(t != f and t.startswith(f + os.sep) for f in final):
            continue
        final.append(t)
    if permanent:
        errs = []
        for t in final:
            try:
                if os.path.isdir(t):
                    shutil.rmtree(t, ignore_errors=False)
                else:
                    os.remove(t)
            except Exception as e:
                errs.append("%s: %s" % (os.path.basename(t), e))
        if errs:
            return {"ok": False, "count": len(final) - len(errs),
                    "msg": "部分删除失败：" + "；".join(errs[:5])}
        return {"ok": True, "count": len(final), "msg": "已永久删除 %d 项" % len(final)}
    ok, m = _send_to_trash(final)
    if not ok:
        return {"ok": False, "msg": "移入回收站失败：" + (m or "未知错误")}
    return {"ok": True, "count": len(final), "msg": "已移入回收站 %d 项" % len(final)}


def local_models_rename(rel, new_name, scope="models"):
    invalidate_local_usage()
    sc = _local_scope(scope)
    rn = _local_root_name(sc)
    root = os.path.abspath(_local_root(sc))
    a = _local_abs(rel, sc)
    if not a or not os.path.exists(a):
        return {"ok": False, "msg": "目标不存在"}
    a = os.path.abspath(a)
    if a == root:
        return {"ok": False, "msg": "不能重命名 %s 根目录" % rn}
    new_name = (new_name or "").strip()
    if not new_name or new_name in (".", ".."):
        return {"ok": False, "msg": "新名称无效"}
    if any(c in new_name for c in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')):
        return {"ok": False, "msg": '新名称不能包含 \\ / : * ? " < > |'}
    dst = os.path.join(os.path.dirname(a), new_name)
    if os.path.abspath(dst) == a:
        return {"ok": True, "rel": rel, "msg": "名称未变化"}
    if os.path.exists(dst):
        return {"ok": False, "msg": "已存在同名项：" + new_name}
    try:
        os.rename(a, dst)
    except Exception as e:
        return {"ok": False, "msg": "重命名失败：%s" % e}
    new_rel = os.path.relpath(dst, root).replace(os.sep, "/")
    return {"ok": True, "rel": new_rel, "msg": "已重命名为 " + new_name}


def _unique_path(dst):
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        cand = "%s(%d)%s" % (base, i, ext)
        if not os.path.exists(cand):
            return cand
        i += 1


def local_models_upload(rel="", files=None, scope="models", rel_paths=None):
    """上传单个或多个文件到本地模型 / 插件目录（rel 为目标目录，空 = 根目录）

    - 默认只取文件名的 basename，杜绝路径穿越
    - 传了 rel_paths（浏览器 webkitRelativePath）时按原目录结构还原（支持"上传文件夹"）
    - 目标已存在同名文件时**不覆盖**，自动加 (1)(2) 序号
    - 返回 results 逐项结果，前端只展示汇总 msg
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _save_upload_batch
    sc = _local_scope(scope)
    dest = _local_abs(rel, sc)
    if not dest:
        return {"ok": False, "msg": "目标目录不合法"}
    if not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录不存在：" + (rel or _local_root_name(sc))}
    r = _save_upload_batch(dest, files, rel_paths)
    if not r["saved"] and not r["failed"]:
        return {"ok": False, "msg": "未选择文件"}
    invalidate_local_usage()
    parts = ["已上传 %d 个文件到 %s" % (r["saved"], _local_rel(rel) or (_local_root_name(sc) + " 根目录"))]
    if r["renamed"]:
        parts.append("%d 个同名文件已自动加序号" % r["renamed"])
    if r["failed"]:
        parts.append("%d 个失败" % r["failed"])
    return {"ok": True, "saved": r["saved"], "renamed": r["renamed"], "failed": r["failed"],
            "results": r["results"], "msg": "，".join(parts)}


def local_models_mkdir(rel="", name="", scope="models"):
    """在本地模型 / 插件目录下新建文件夹（rel 为父目录，空 = 根目录）"""
    sc = _local_scope(scope)
    nm = (name or "").strip().rstrip(". ")
    if not nm or nm in (".", ".."):
        return {"ok": False, "msg": "文件夹名不能为空"}
    if any(ch in nm for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|")):
        return {"ok": False, "msg": "文件夹名不能包含 \\ / : * ? \" < > | 等字符"}
    parent = _local_abs(rel, sc)
    if not parent:
        return {"ok": False, "msg": "目标目录不合法"}
    if not os.path.isdir(parent):
        return {"ok": False, "msg": "目标目录不存在"}
    tgt = os.path.join(parent, nm)
    if os.path.exists(tgt):
        return {"ok": False, "msg": "「%s」已存在" % nm}
    try:
        os.makedirs(tgt)
    except Exception as e:
        return {"ok": False, "msg": str(e)}
    invalidate_local_usage()
    rel_new = ((_local_rel(rel) or "") + "/" + nm).lstrip("/")
    return {"ok": True, "msg": "已新建文件夹「%s」" % nm, "rel": rel_new}


def local_models_move(rels, dest_rel, scope="models"):
    """把选中的文件/文件夹移动到根目录下的另一个子目录"""
    invalidate_local_usage()
    sc = _local_scope(scope)
    rn = _local_root_name(sc)
    root = os.path.abspath(_local_root(sc))
    dest = _local_abs(dest_rel, sc)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标文件夹不存在"}
    dest_abs = os.path.abspath(dest)
    if isinstance(rels, str):
        rels = [rels]
    if not rels:
        return {"ok": False, "msg": "未选择任何文件"}
    moved, skipped, errs = 0, 0, []
    for rel in rels:
        a = _local_abs(rel, sc)
        if not a or not os.path.exists(a):
            errs.append("%s 不存在" % rel)
            continue
        a_abs = os.path.abspath(a)
        if a_abs == root:
            errs.append("不能移动 %s 根目录" % rn)
            continue
        if os.path.dirname(a_abs) == dest_abs:
            skipped += 1
            continue
        if dest_abs == a_abs or dest_abs.startswith(a_abs + os.sep):
            errs.append("%s 不能移动到自身或其子目录" % os.path.basename(a_abs))
            continue
        try:
            shutil.move(a_abs, _unique_path(os.path.join(dest_abs, os.path.basename(a_abs))))
            moved += 1
        except Exception as e:
            errs.append("%s: %s" % (os.path.basename(a_abs), e))
    msg = "已移动 %d 项" % moved
    if skipped:
        msg += "，%d 项已在目标目录" % skipped
    if errs:
        return {"ok": moved > 0, "moved": moved, "msg": msg + "；失败：" + "；".join(errs[:5])}
    return {"ok": True, "moved": moved, "msg": msg}


def open_path_in_explorer(path, select=False):
    """在系统文件管理器中打开路径；select=True 时定位到该项"""
    if not path:
        return {"ok": False, "msg": "路径为空"}
    if not os.path.exists(path):
        return {"ok": False, "msg": "路径不存在：" + path}
    try:
        if IS_WINDOWS:
            args = ["explorer", "/select,", os.path.normpath(path)] if select \
                else ["explorer", os.path.normpath(path)]
            subprocess.Popen(args, shell=False)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if select else ["open", path])
        else:
            subprocess.Popen(["xdg-open", path if not select else os.path.dirname(path)])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def local_models_folder_tree(limit=3000, max_depth=4, scope="models"):
    """返回根目录下所有子文件夹（供「移动到」选择目标）"""
    sc = _local_scope(scope)
    rn = _local_root_name(sc)
    root = _local_root(sc)
    if not os.path.isdir(root):
        return {"ok": False, "msg": "%s 目录不存在：%s" % (rn, root), "items": []}
    # 宽搜：先收完浅层文件夹再往下，避免深层子目录把 limit 撑满（插件目录有几级深）
    items = []
    level = [(root, "", 0)]
    while level and len(items) < limit:
        nxt = []
        for cur, rel, dep in level:
            if len(items) >= limit:
                break
            items.append({"rel": rel, "name": (os.path.basename(cur) if rel else rn),
                          "depth": dep})
            if dep + 1 >= max_depth:
                continue
            try:
                names = sorted(os.listdir(cur), key=lambda s: s.lower())
            except Exception:
                continue
            for nm in names:
                p = os.path.join(cur, nm)
                try:
                    if os.path.isdir(p):
                        nxt.append((p, (rel + "/" + nm) if rel else nm, dep + 1))
                except Exception:
                    continue
        level = nxt
    items.sort(key=lambda x: (x["depth"], x["rel"].lower()))
    return {"ok": True, "items": items, "root": root, "root_name": rn, "scope": sc}


def local_models_open(rel="", select=False, scope="models"):
    p = _local_abs(rel, scope)
    if not p:
        return {"ok": False, "msg": "非法路径"}
    return open_path_in_explorer(p, select)
