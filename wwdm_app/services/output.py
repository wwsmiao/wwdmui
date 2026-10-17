# -*- coding: utf-8 -*-
"""输出/输入目录域：目录浏览、文件类型识别、zip 打包下载、上传落盘。

自 services/__init__.py 拆出（v5.0 第 6 步 域 8/11），函数体逐字未改。

这里也是「相对路径安全」的家：_safe_rel / _is_inside / _safe_path 三个函数
是全仓唯一的路径校验入口，files 与 workflows 两个域都用 `from . import _safe_path`
延迟引用（本包末尾再导出把它们并回命名空间）。
⚠️ 刻意不做 realpath：junction 跨盘是受支持的部署方式。
"""
import logging
import os
import shutil
import time
import zipfile

log = logging.getLogger("wwdm.services")   # 与 services 包同一个 logger 对象


# ============================================================
# 输出管理 — 文件浏览
# ============================================================

# 文件类型识别
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico"}
_VID_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"}
_AUD_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma", ".opus"}


def _file_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in _IMG_EXTS:
        return "image"
    if ext in _VID_EXTS:
        return "video"
    if ext in _AUD_EXTS:
        return "audio"
    return "other"


def _safe_rel(rel_path):
    """把前端传来的相对路径规范化成安全形式；非法一律返回 None。

    比「先拼好再 commonpath」更早、更明确地拦下这些：
      * ``..`` 段（路径穿越）—— 含 Windows 会当成 ``..`` 处理的 ``.. `` / ``.. .``
      * 前导 ``/`` 或 ``\\``（绝对路径 / UNC）
      * 任意段里出现 ``:``（盘符 / 驱动器相对路径 / NTFS 数据流 ``a.txt:ads``）
      * NUL 字节（Windows 的 abspath 不拒绝它，会一路漏到 open() 才炸成 500）

    合法时返回以 ``/`` 分隔的相对路径；空串表示「根本身」。
    """
    r = str(rel_path or "").replace("\\", "/")
    if not r:
        return ""
    if "\x00" in r:
        return None
    if r.startswith("/"):
        return None
    parts = []
    for seg in r.split("/"):
        if seg in ("", "."):
            continue
        # Windows 的文件名规则里，分量末尾的空格与点会被忽略，所以先按该口径归一：
        #   ".." / ".. " / ".. ." / "... " 全部归一成空串（= 不表达任何层级），
        #   而 "a." → "a"、"a..b" 不受影响。
        # 归一后剩下空串的分量（纯点/纯空格）内部会整个丢掉它，正常文件名也不可能
        # 长成这样，所以直接判非法 —— 比「先放行、指望后面 commonpath 兜住」清楚。
        if not seg.rstrip(" ."):
            return None
        if ":" in seg:
            return None
        parts.append(seg)
    return "/".join(parts)


def _is_inside(root, target):
    """target 是否落在 root 内。

    跨盘符 / UNC 与本地盘混用时 ``os.path.commonpath`` 会抛
    ``ValueError: Paths don't have the same drive``。旧实现让它直接冒到全局
    错误处理器，于是 ``GET /api/output/browse?path=D:\\`` 这种请求变成 500
    —— 局域网里谁都打得出来。这里统一吞成 False（= 越界）。
    """
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def _safe_path(root_dir, rel_path):
    """防御路径穿越：把 rel_path 解析到 root_dir 内；越界 / 非法一律返回 None。

    这是全项目**唯一**的相对路径校验入口 —— ``_local_abs``（本地模型/插件那套）、
    ``_save_upload_batch``（上传落盘）都走它，所以新增路由只要调用带路径参数的
    服务函数就自动受保护，不必再各写一份 commonpath。

    关于软链接（特意不处理）
    ------------------------
    这里**刻意不做 realpath**：「模型目录用目录联接（junction）指到别的盘」是本
    项目常见且受支持的部署方式，realpath 之后父目录与目标的盘符会不一致，会把正常
    目录误判成越界。而「在受管目录里放一个指向外部的链接」这条绕行，前提是攻击者
    已经能在受管目录里创建链接 —— 那已经等价于本机文件系统写权限，再拦这一层没有
    额外收益，却会实打实地弄坏正常部署。
    """
    root = os.path.abspath(root_dir)
    r = _safe_rel(rel_path)
    if r is None:
        return None
    target = os.path.abspath(os.path.join(root, r.replace("/", os.sep)))
    if not _is_inside(root, target):
        return None
    return target


def _fuzzy_match(query, name):
    """模糊匹配：query 整体是 name 子串，或 query 各字符按顺序出现在 name 中（不区分大小写）"""
    q = query.lower()
    n = name.lower()
    if q in n:
        return True
    idx = 0
    for ch in q:
        idx = n.find(ch, idx)
        if idx == -1:
            return False
        idx += 1
    return True


def _sort_items(items, sort):
    """排序：name 按名称、date 按修改时间；_desc 表示倒序（date_desc = 最新在前）"""
    rev = sort.endswith("_desc")
    if sort.startswith("name"):
        items.sort(key=lambda x: x.get("name", "").lower(), reverse=rev)
    elif sort.startswith("date"):
        items.sort(key=lambda x: x.get("modified", 0), reverse=rev)
    else:
        items.sort(key=lambda x: x.get("name", "").lower())
    # 稳定二次排序：目录在前，文件在后（不改变组内已排好的顺序）
    items.sort(key=lambda x: 0 if x.get("is_dir") else 1)
    return items


def recursive_search(root_dir, query, sort, page, page_size, type_fn):
    """跨文件夹模糊搜索文件名，返回与 browse 相同的分页结构（带 recursive 标记）"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for name in dirnames:
            if not _fuzzy_match(query, name):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            items.append({"name": name, "path": rel, "modified": st.st_mtime, "is_dir": True})
        for name in filenames:
            if not _fuzzy_match(query, name):
                continue
            ft = type_fn(name)
            if ft == "other":
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_dir).replace("\\", "/")
            items.append({"name": name, "path": rel, "type": ft, "size": st.st_size,
                          "modified": st.st_mtime, "is_dir": False})
    _sort_items(items, sort)
    total = len(items)
    ps = max(1, min(100, int(page_size)))
    tp = max(1, (total + ps - 1) // ps) if total else 1
    pg = max(1, min(tp, int(page)))
    start = (pg - 1) * ps
    return {
        "ok": True, "current_path": "", "breadcrumbs": [], "parent_path": None,
        "items": items[start:start + ps], "page": pg, "page_size": ps,
        "total": total, "total_pages": tp, "search": query, "sort": sort,
        "recursive": True,
    }


def browse_output_dir(root_dir, rel_path="", page=1, page_size=20,
                      search="", sort="date_desc"):
    """浏览输出目录：文件夹在前、文件在后，支持分页/搜索/排序。
    带 search 时执行跨文件夹模糊搜索。"""
    if search and search.strip():
        return recursive_search(root_dir, search.strip(), sort, page, page_size, _file_type)
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

    # 枚举
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
            ft = _file_type(name)
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


def serve_output_file(root_dir, rel_path):
    """安全返回输出文件绝对路径（供 send_file 使用）"""
    target = _safe_path(root_dir, rel_path)
    if not target or not os.path.isfile(target):
        return None
    return target


class _ZipSink:
    """zipfile 的输出漏斗：把写进来的字节攒成块，交给生成器逐块 yield。

    故意不实现 seek/tell：zipfile 会自动降级成「不可 seek」写法（用数据描述符记录
    每个条目的长度），这正是流式 zip 需要的行为。
    """

    def __init__(self):
        self.chunks = []

    def write(self, b):
        if b:
            self.chunks.append(bytes(b))
        return len(b)

    def flush(self):
        pass

    def drain(self):
        out = self.chunks
        self.chunks = []
        return out


def zip_collect(root_dir, rel_paths):
    """收集要打包的 (绝对路径, zip 内相对路径) 列表"""
    items = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            continue
        if os.path.isdir(t):
            for dirpath, _dirnames, filenames in os.walk(t):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    items.append((full, os.path.relpath(full, root_dir).replace("\\", "/")))
        else:
            items.append((t, os.path.relpath(t, root_dir).replace("\\", "/")))
    return items


_ZIP_CHUNK = 1 << 20          # 每次读写 1 MB：内存占用与文件大小无关


def _zip_one(zf, sink, srcf, arc, chunk=_ZIP_CHUNK):
    """（生成器）把一个文件写进 zip，边写边把攒下的字节交出去。

    ⚠️ 这里刻意不用 ``zf.write()``：它会**把整个文件压完才返回**，于是 _ZipSink
    期间就攒下了一整个文件的字节 —— 实测 40MB 文件堆峰值 40.4MB，等于没改善，
    单个 10GB 模型照样顶爆内存。自己分块读写、每块 drain 一次，内存只与 chunk 有关。
    """
    st = os.fstat(srcf.fileno())
    zi = zipfile.ZipInfo(arc, date_time=time.localtime(st.st_mtime)[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = (0o100000 | (st.st_mode & 0o777)) << 16      # 保留权限位
    with zf.open(zi, "w") as dst:
        while True:
            data = srcf.read(chunk)
            if not data:
                break
            dst.write(data)
            for c in sink.drain():
                yield c
    for c in sink.drain():
        yield c


def zip_files_stream(root_dir, rel_paths, zip_name="files.zip"):
    """流式打包：边压缩边吐，不再把整个 zip 先攒进内存。

    原实现用 BytesIO 全量入内存，用户选中一个几百 GB 的输出目录再点下载就会把内存顶爆。
    返回 (items, generator)：
      * ``items`` 先算好，调用方用它判断「有没有可下载的内容」（空则回 404），
        同时避免「开始传输后没法再改状态码」的问题；
      * ``generator`` 逐块产出 zip 字节，交给 Flask 分块传输。
    压缩方式与原来一致（ZIP_DEFLATED）。
    """
    items = zip_collect(root_dir, rel_paths)

    def _gen():
        sink = _ZipSink()
        done, skipped = 0, 0
        try:
            with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
                for full, arc in items:
                    try:
                        srcf = open(full, "rb")
                    except OSError as e:
                        # 还没往 zip 里写过任何东西，跳过是安全的
                        log.warning("[打包] 跳过 %s: %s", full, e)
                        skipped += 1
                        continue
                    try:
                        for ch in _zip_one(zf, sink, srcf, arc):
                            yield ch
                        done += 1
                    finally:
                        srcf.close()
            # with 退出时会写入中央目录，必须再吐一次
            for ch in sink.drain():
                yield ch
        except Exception as e:
            # 已经开始传输了，状态码改不了；如实记日志（客户端会看到下载中断）
            log.error("[打包] 生成中断: %s", e)
        finally:
            log.info("[打包] %s 结束：写出 %d 个，跳过 %d 个，共 %d 个",
                     zip_name, done, skipped, len(items))

    return items, _gen()


def delete_output_files(root_dir, rel_paths):
    """批量删除文件/文件夹"""
    results = []
    for rp in rel_paths:
        t = _safe_path(root_dir, rp)
        if not t or not os.path.exists(t):
            results.append({"path": rp, "ok": False, "msg": "路径无效或不存在"})
            continue
        try:
            if os.path.isdir(t):
                shutil.rmtree(t)
            else:
                os.remove(t)
            results.append({"path": rp, "ok": True, "msg": "已删除"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def rename_output_file(root_dir, rel_path, new_name):
    """重命名文件/文件夹"""
    t = _safe_path(root_dir, rel_path)
    if not t or not os.path.exists(t):
        return {"ok": False, "msg": "路径无效或不存在"}
    safe_name = os.path.basename(new_name)
    if not safe_name or safe_name in (".", ".."):
        return {"ok": False, "msg": "无效的文件名"}
    new_t = os.path.join(os.path.dirname(t), safe_name)
    if os.path.exists(new_t):
        return {"ok": False, "msg": "目标已存在: " + safe_name}
    try:
        os.rename(t, new_t)
        return {"ok": True, "msg": "已重命名为 " + safe_name}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def move_output_files(root_dir, rel_paths, dest_rel):
    """批量移动文件/文件夹到目标目录"""
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


def copy_output_files(root_dir, rel_paths, dest_rel):
    """批量复制文件/文件夹到目标目录"""
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


def send_to_input_dir(output_root, rel_paths, input_root):
    """将输出目录的文件/文件夹复制到输入目录（跨根目录复制）"""
    if not os.path.isdir(input_root):
        os.makedirs(input_root, exist_ok=True)
    results = []
    for rp in rel_paths:
        src = _safe_path(output_root, rp)
        if not src or not os.path.exists(src):
            results.append({"path": rp, "ok": False, "msg": "源路径无效"})
            continue
        # 目标: input_root / basename
        dst = os.path.join(input_root, os.path.basename(src))
        try:
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                os.makedirs(input_root, exist_ok=True)
                shutil.copy2(src, dst)
            results.append({"path": rp, "ok": True, "msg": "已发送"})
        except Exception as e:
            results.append({"path": rp, "ok": False, "msg": str(e)})
    return {"ok": True, "results": results}


def create_output_dir(root_dir, rel_path):
    """创建新目录"""
    t = _safe_path(root_dir, rel_path)
    if not t:
        return {"ok": False, "msg": "无效路径"}
    if os.path.exists(t):
        return {"ok": False, "msg": "目录已存在"}
    try:
        os.makedirs(t)
        return {"ok": True, "msg": "目录已创建"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def _safe_upload_rel(rel):
    """把浏览器的 webkitRelativePath 规范化成安全相对路径；非法返回 None

    - 忽略空段与 "."
    - ".." / 盘符 / 绝对路径一律判非法
    """
    parts = []
    for seg in str(rel or "").replace("\\", "/").split("/"):
        seg = seg.strip()
        if not seg or seg == ".":
            continue
        if seg == ".." or ":" in seg:
            return None
        parts.append(seg)
    return "/".join(parts)


def _save_upload_batch(dest_abs, files, rel_paths=None):
    """统一落盘：支持按 webkitRelativePath 还原目录结构，同名不覆盖（自动加序号）

    返回 {"saved","renamed","failed","results"}；调用方自行组装 msg。
    """
    # 延迟导入：这些符号仍住在 services/__init__.py
    from . import _unique_path
    base = os.path.abspath(dest_abs)
    results = []
    saved = renamed = failed = 0
    for i, f in enumerate(files or []):
        raw = os.path.basename((getattr(f, "filename", "") or "").replace("\\", "/").strip())
        if not raw or raw in (".", ".."):
            continue
        rel = ""
        if rel_paths and i < len(rel_paths):
            rel = _safe_upload_rel(rel_paths[i])
            if rel is None:
                failed += 1
                results.append({"name": raw, "ok": False, "msg": "路径不合法，已跳过"})
                continue
        if rel:
            tgt = _safe_path(base, rel)
            if not tgt:
                failed += 1
                results.append({"name": rel, "ok": False, "msg": "路径越界，已跳过"})
                continue
        else:
            # 原来这里直接 join：filename 是 "C:x" 时 join 会得到驱动器相对路径，
            # 落到 C 盘当前目录去。统一过一遍 _safe_path。
            tgt = _safe_path(base, raw)
            if not tgt:
                failed += 1
                results.append({"name": raw, "ok": False, "msg": "文件名不合法，已跳过"})
                continue
        tgt = _unique_path(tgt)
        try:
            sub = os.path.dirname(tgt)
            if sub and not os.path.isdir(sub):
                os.makedirs(sub, exist_ok=True)
            f.save(tgt)
        except Exception as e:
            failed += 1
            results.append({"name": raw, "ok": False, "msg": str(e)})
            continue
        saved += 1
        show = os.path.relpath(tgt, base).replace(os.sep, "/")
        if os.path.basename(tgt) != raw:
            renamed += 1
            results.append({"name": show, "ok": True, "msg": "已上传（同名，自动改名）"})
        else:
            results.append({"name": show, "ok": True, "msg": "已上传"})
    return {"saved": saved, "renamed": renamed, "failed": failed, "results": results}


def upload_output_files(root_dir, dest_rel, files, rel_paths=None):
    """上传单个/多个文件到指定根目录（可选按 webkitRelativePath 还原目录结构）"""
    dest = _safe_path(root_dir, dest_rel)
    if not dest or not os.path.isdir(dest):
        return {"ok": False, "msg": "目标目录无效"}
    r = _save_upload_batch(dest, files, rel_paths)
    if not r["saved"] and not r["failed"]:
        return {"ok": False, "msg": "未选择文件"}
    parts = ["已上传 %d 个文件" % r["saved"]]
    if r["renamed"]:
        parts.append("%d 个同名文件已自动加序号" % r["renamed"])
    if r["failed"]:
        parts.append("%d 个失败" % r["failed"])
    return {"ok": True, "saved": r["saved"], "renamed": r["renamed"],
            "failed": r["failed"], "results": r["results"], "msg": "，".join(parts)}
