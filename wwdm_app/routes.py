import os, time, threading, subprocess, platform as _platform
from flask import request, jsonify, render_template, send_file, Response
from . import config
from . import database as db
from . import services as svc
from . import security as sec


def _zip_response(gen, name):
    """流式 zip 响应：边压缩边分块发送，不再先把整包读进内存。

    注意这里**不能用 stream_with_context**：生成器内部不碰 request/g，
    只用到模块级的 settings 与日志，所以请求上下文结束也能继续迭代。
    """
    return Response(gen, mimetype="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name})


def _scope_arg(raw):
    """本地浏览作用域：models（ComfyUI/models）/ plugins（ComfyUI/custom_nodes）"""
    s = (raw or "").strip().lower()
    return s if s in ("models", "plugins") else "models"


# ---- 外部工具版本探测缓存 ----
# git / python / aria2c 的 --version 每次都要 fork/exec 一个进程（Windows 上 50~300ms），
# 而 checkGit()/detectPy() 在每次进插件页、每次点克隆前都会调一次，属纯重复劳动。
# 命中缓存后这些接口从「几十~几百 ms」降到「<1ms」。失败结果只留 10s，便于装完工具后立刻生效。
_tool_ver_cache = {}
_tool_ver_lock = threading.Lock()


def _tool_version_cached(key, argv, ttl=300.0):
    """带 TTL 的命令版本探测；返回版本首行，失败返回 None"""
    now = time.time()
    with _tool_ver_lock:
        c = _tool_ver_cache.get(key)
        if c and now - c[0] < c[2]:
            return c[1]
    out = None
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=5)
        if r.returncode == 0:
            out = (r.stdout or r.stderr).split("\n")[0].strip() or None
    except Exception:
        out = None
    with _tool_ver_lock:
        _tool_ver_cache[key] = (now, out, ttl if out else 10.0)
    return out


def register(app):
    """将所有路由注册到 Flask app"""

    @app.route("/")
    def index():
        return render_template("index.html")

    # ========== 模型 API ==========
    @app.route("/api/models", methods=["GET"])
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

    @app.route("/api/models", methods=["POST"])
    def api_add():
        d = request.get_json(force=True, silent=True)
        if not d or not d.get("name") or not d.get("save_path") or not d.get("download_url"):
            return jsonify({"ok": False, "msg": "参数不完整"}), 400
        rid = db.db_add(d["name"].strip(), d["save_path"].strip(), d["download_url"].strip())
        return jsonify({"ok": True, "id": rid}) if rid else (jsonify({"ok": False, "msg": "URL已存在"}), 400)

    @app.route("/api/models/extract_names", methods=["POST"])
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

    @app.route("/api/models/batch", methods=["POST"])
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

    @app.route("/api/models/<int:rid>", methods=["GET"])
    def api_get(rid):
        row = db.db_get(rid)
        return jsonify(row) if row else (jsonify({"error": "not found"}), 404)

    @app.route("/api/models/<int:rid>", methods=["PUT"])
    def api_update(rid):
        d = request.get_json(force=True, silent=True)
        if not d:
            return jsonify({"ok": False}), 400
        db.db_update(rid, d["name"].strip(), d["save_path"].strip(), d["download_url"].strip())
        return jsonify({"ok": True})

    @app.route("/api/models/<int:rid>", methods=["DELETE"])
    def api_delete(rid):
        db.db_delete(rid)
        return jsonify({"ok": True})

    # ========== 本地模型文件 API（直接操作 ComfyUI/models 目录） ==========
    @app.route("/api/models/local/list", methods=["GET"])
    def api_local_list():
        return jsonify(svc.local_models_list(
            request.args.get("rel", ""), request.args.get("sort", "name_asc"),
            _scope_arg(request.args.get("scope")),
            with_stats=request.args.get("stats") == "1"))

    @app.route("/api/models/local/stats", methods=["GET"])
    def api_local_stats():
        """目录递归统计（较慢，前端异步补状态栏与文件夹卡片数字）"""
        return jsonify(svc.local_dir_stats(request.args.get("rel", ""),
                                           _scope_arg(request.args.get("scope"))))

    @app.route("/api/models/local/search", methods=["GET"])
    def api_local_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"ok": False, "msg": "请输入搜索关键字"}), 400
        sc = _scope_arg(request.args.get("scope"))
        return jsonify(svc.local_models_search(
            q, request.args.get("rel", ""), sort=request.args.get("sort", "size_desc"),
            scope=sc, include_dirs=(sc == "plugins")))

    @app.route("/api/models/local/delete", methods=["POST"])
    def api_local_delete():
        d = request.get_json(force=True, silent=True) or {}
        rels = d.get("rels") or []
        if not isinstance(rels, list) or not rels:
            return jsonify({"ok": False, "msg": "未选择任何文件"}), 400
        return jsonify(svc.local_models_delete(rels, bool(d.get("permanent")),
                                               _scope_arg(d.get("scope"))))

    @app.route("/api/models/local/rename", methods=["POST"])
    def api_local_rename():
        d = request.get_json(force=True, silent=True) or {}
        return jsonify(svc.local_models_rename(d.get("rel", ""), d.get("new_name", ""),
                                               _scope_arg(d.get("scope"))))

    @app.route("/api/models/local/move", methods=["POST"])
    def api_local_move():
        d = request.get_json(force=True, silent=True) or {}
        rels = d.get("rels") or []
        if not isinstance(rels, list) or not rels:
            return jsonify({"ok": False, "msg": "未选择任何文件"}), 400
        return jsonify(svc.local_models_move(rels, d.get("dest", ""),
                                             _scope_arg(d.get("scope"))))

    @app.route("/api/models/local/move_targets", methods=["GET"])
    def api_local_move_targets():
        """返回根目录下所有文件夹，供「移动到」选择"""
        return jsonify(svc.local_models_folder_tree(
            scope=_scope_arg(request.args.get("scope"))))
    @app.route("/api/models/local/open", methods=["GET"])
    def api_local_open():
        return jsonify(svc.local_models_open(request.args.get("rel", ""),
                                             request.args.get("select", "0") == "1",
                                             _scope_arg(request.args.get("scope"))))

    @app.route("/api/models/local/upload", methods=["POST"])
    def api_local_upload():
        """上传单个或多个文件到本地模型 / 插件目录（multipart/form-data：files, dest, scope）"""
        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "msg": "未选择文件"}), 400
        return jsonify(svc.local_models_upload((request.form.get("dest") or "").strip(),
                                               files, _scope_arg(request.form.get("scope")),
                                               request.form.getlist("paths")))

    @app.route("/api/models/local/mkdir", methods=["POST"])
    def api_local_mkdir():
        """在本地模型 / 插件目录下新建文件夹（JSON：rel, name, scope）"""
        d = request.get_json(silent=True) or {}
        return jsonify(svc.local_models_mkdir(d.get("rel", ""), d.get("name", ""),
                                              _scope_arg(d.get("scope"))))

    @app.route("/api/paths", methods=["GET"])
    def api_paths():
        return jsonify(db.db_get_paths())

    # ========== aria2c API ==========
    @app.route("/api/aria2/start", methods=["POST"])
    def api_a2_start():
        ok, msg = svc.aria2_start()
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/aria2/stop", methods=["POST"])
    def api_a2_stop():
        return jsonify({"ok": True, "msg": svc.aria2_stop()})

    @app.route("/api/aria2/status", methods=["GET"])
    def api_a2_status():
        r, e = svc.aria2_rpc("aria2.getVersion")
        return jsonify({"running": True, "version": r.get("version", "?")}) if not e else jsonify({"running": False})

    @app.route("/api/aria2/detect", methods=["GET"])
    def api_a2_detect():
        path = svc.get_aria2c_path()
        found = os.path.isfile(path)
        if not found and config.IS_LINUX:
            try:
                rr = subprocess.run(["which", "aria2c"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                if rr.returncode == 0:
                    path = rr.stdout.strip()
                    found = True
            except Exception:
                pass
        ver = _tool_version_cached("aria2:" + str(path), [path, "--version"]) if found else None
        return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

    # ========== 下载 API ==========
    @app.route("/api/download/start/<int:rid>", methods=["POST"])
    def api_dl_start(rid):
        d = request.get_json(silent=True) or {}
        ok, msg = svc.start_download(rid, use_mirror=bool(d.get("mirror")))
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/download/batch", methods=["POST"])
    def api_dl_batch():
        d = request.get_json(silent=True) or {}
        rids = d.get("ids", [])
        if not isinstance(rids, list) or not rids:
            return jsonify({"ok": False, "msg": "ids无效"}), 400
        return jsonify({"ok": True, "results": svc.start_batch(rids, bool(d.get("mirror")))})

    @app.route("/api/download/pause/<int:rid>", methods=["POST"])
    def api_dl_pause(rid):
        ok, msg = svc.pause_download(rid)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/download/resume/<int:rid>", methods=["POST"])
    def api_dl_resume(rid):
        ok, msg = svc.resume_download(rid)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/download/cancel/<int:rid>", methods=["POST"])
    def api_dl_cancel(rid):
        ok, msg = svc.cancel_download(rid)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/download/cancel_queue/<int:rid>", methods=["POST"])
    def api_dl_cancel_queue(rid):
        with svc.queue_lock:
            if rid in svc.download_queue:
                svc.download_queue.remove(rid)
                svc.download_mirrors.pop(rid, None)
                return jsonify({"ok": True, "msg": "已从队列移除"})
        return jsonify({"ok": False, "msg": "不在队列中"})

    @app.route("/api/download/status/<int:rid>", methods=["GET"])
    def api_dl_status(rid):
        st = svc.get_download_status(rid)
        return jsonify({"status": "none"}) if st is None else jsonify(st)

    @app.route("/api/download/all_status", methods=["GET"])
    def api_dl_all_status():
        return jsonify(svc.get_all_download_status())

    # ========== 插件 API ==========
    @app.route("/api/plugins", methods=["GET"])
    def api_plugins():
        kw = request.args.get("q", "").strip()
        page = max(int(request.args.get("page", 1)), 1)
        page_size = max(min(int(request.args.get("page_size", 20)), 500), 10)
        sort = request.args.get("sort", "id_asc")
        installed_only = request.args.get("installed", "") == "1"
        installed = db.plugin_get_names()
        offset = (page - 1) * page_size
        fnames = list(installed.keys()) if installed_only else None

        if fnames is not None and len(fnames) <= 900:
            # 「已安装」过滤下推到 SQL：一次带 WHERE name IN (...) 的查询 + COUNT，
            # 不再把整表 5000+ 行拉进 Python 再过滤分页（SQLite 变量上限 999，留余量）
            plist = db.plugin_search(kw, sort=sort, limit=page_size, offset=offset, names=fnames)
            total = db.plugin_count(kw, names=fnames)
        elif installed_only:
            # 兜底：已安装项超过 900 个时退回内存过滤
            plist = db.plugin_search(kw, sort=sort, limit=99999, offset=0)
            for p in plist:
                p["installed"] = p["name"] in installed
            plist = [p for p in plist if p["installed"]]
            total = len(plist)
            plist = plist[offset:offset + page_size]
        else:
            plist = db.plugin_search(kw, sort=sort, limit=page_size, offset=offset)
            total = db.plugin_count(kw)

        for p in plist:
            p["installed"] = p["name"] in installed

        for p in plist:
            if "repo_url" not in p:
                p["repo_url"] = p.get("url", "")
            if "desc" not in p:
                p["desc"] = p.get("description", "")
        return jsonify({"items": plist, "total": total, "page": page, "page_size": page_size,
                        "total_pages": max((total + page_size - 1) // page_size, 1)})

    @app.route("/api/plugins", methods=["POST"])
    def api_plugin_add():
        d = request.get_json(force=True, silent=True)
        if not d or not d.get("name") or not d.get("url"):
            return jsonify({"ok": False, "msg": "参数不完整"}), 400
        pid = db.plugin_add(d["name"].strip(), d["url"].strip(), d.get("description", "").strip())
        return jsonify({"ok": True, "id": pid})

    @app.route("/api/plugins/<int:pid>", methods=["GET"])
    def api_plugin_get(pid):
        p = db.plugin_get(pid)
        if not p:
            return jsonify({"ok": False, "msg": "未找到"}), 404
        installed = db.plugin_get_names()
        p["installed"] = p["name"] in installed
        p["repo_url"] = p.get("url", "")
        p["desc"] = p.get("description", "")
        return jsonify(p)

    @app.route("/api/plugins/<int:pid>", methods=["PUT"])
    def api_plugin_update(pid):
        d = request.get_json(force=True, silent=True)
        if not d:
            return jsonify({"ok": False}), 400
        db.plugin_update(pid, d["name"].strip(), d["url"].strip(), d.get("description", "").strip())
        return jsonify({"ok": True})

    @app.route("/api/plugins/<int:pid>", methods=["DELETE"])
    def api_plugin_delete(pid):
        p = db.plugin_get(pid)
        if not p:
            return jsonify({"ok": False, "error": "插件不存在"}), 404
        installed = db.plugin_get_names()
        if p["name"] not in installed:
            return jsonify({"ok": False, "error": "该插件未安装"}), 400
        # 卸载只删除插件文件夹，保留插件数据库中的记录（便于重新克隆/查询）
        ok, msg = svc.plugin_uninstall(p["name"])
        if not ok:
            return jsonify({"ok": False, "error": msg}), 500
        return jsonify({"ok": True, "msg": msg + "（数据库记录已保留）"})

    @app.route("/api/plugins/installed", methods=["GET"])
    def api_plugin_installed():
        return jsonify(db.plugin_get_names())

    @app.route("/api/plugins/open_dir")
    def api_plugin_open_dir():
        """在资源管理器中打开 custom_nodes 目录"""
        cn_dir = os.path.join(svc.get_comfyui_dir(), "custom_nodes")
        if os.path.isdir(cn_dir):
            try:
                subprocess.Popen(['explorer', cn_dir], shell=False)
            except Exception:
                os.startfile(cn_dir)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "custom_nodes 目录不存在"})

    # ========== 插件克隆 API ==========
    @app.route("/api/plugins/clone/<int:pid>", methods=["POST"])
    def api_clone(pid):
        # 镜像一律取全局设置，页面不再传 mirror
        ok, msg = svc.start_clone(pid)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/plugins/clone_batch", methods=["POST"])
    def api_clone_batch():
        d = request.get_json(silent=True) or {}
        pids = d.get("ids", [])
        if not isinstance(pids, list) or not pids:
            return jsonify({"ok": False, "msg": "ids无效"}), 400
        return jsonify({"ok": True, "results": svc.start_clone_batch(pids)})
    @app.route("/api/plugins/clone_url", methods=["POST"])
    def api_clone_by_url():
        """按仓库地址克隆插件（工作流解析弹窗的「下载」按钮）。

        解析给出的仓库来自上游映射表，个别不在插件库列表里 ——
        服务层会先补录条目拿到 id，再走既有的克隆流程。
        """
        d = request.get_json(silent=True) or {}
        url = (d.get("url") or "").strip()
        if not url:
            return jsonify({"ok": False, "msg": "缺少仓库地址"}), 400
        ok, msg, pid = svc.plugins_clone_by_url(url, d.get("name") or "")
        return jsonify({"ok": ok, "msg": msg, "id": pid})

    @app.route("/api/plugins/repair_db", methods=["POST"])
    def api_plugin_repair_db():
        ok, res = db.plugin_repair_db()
        if not ok:
            return jsonify({"ok": False, "msg": res.get("msg", "修复失败")}), 500
        return jsonify({"ok": True, "result": res,
                        "msg": "修复完成：%d → %d 条，去除重复 %d 个" % (
                            res["total_before"], res["total_after"], res["removed_count"])})

    @app.route("/api/plugins/update_library", methods=["POST"])
    def api_plugin_update_library():
        """从 ComfyUI-Manager 官方数据源拉取最新插件库，合并写入 models.db

        写库规则：按规范化仓库地址合并 —— 已有的覆盖名称/描述，没有的新增，
        绝不简单追加；同时重建「节点类名 → 插件仓库」映射表（工作流解析用）。
        """
        res = svc.plugins_update_library()
        if not res.get("ok"):
            return jsonify(res), 502
        return jsonify(res)


    @app.route("/api/plugins/clone_status/<int:pid>", methods=["GET"])
    def api_clone_status(pid):
        return jsonify(svc.get_clone_status(pid))

    @app.route("/api/plugins/clone_all_status", methods=["GET"])
    def api_clone_all_status():
        return jsonify(svc.get_all_clone_status())

    @app.route("/api/plugins/clone_cancel/<int:pid>", methods=["POST"])
    def api_clone_cancel(pid):
        ok, msg = svc.cancel_clone(pid)
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/plugins/clone_cancel_cleanup", methods=["POST"])
    def api_clone_cancel_cleanup():
        """停止单个克隆，并删掉它「克隆到一半」的文件夹（工作流解析弹窗的「停止」）。

        半成品目录留着会让下次克隆残缺或失败，所以这里顺手清掉；
        已克隆完成的插件目录不会被动（口径同「停止克隆」）。
        """
        d = request.get_json(silent=True) or {}
        try:
            pid = int(d.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if not pid:
            return jsonify({"ok": False, "msg": "缺少插件 id"}), 400
        return jsonify(svc.cancel_clone_with_cleanup(pid))

    @app.route("/api/plugins/clone_stop_all", methods=["POST"])
    def api_plugin_clone_stop_all():
        return jsonify(svc.stop_clone_and_cleanup())

    @app.route("/api/plugins/clone_cancel_queue/<int:pid>", methods=["POST"])
    def api_clone_cancel_queue(pid):
        with svc.clone_lock:
            if pid in svc.clone_queue:
                svc.clone_queue.remove(pid)
                return jsonify({"ok": True, "msg": "已从队列移除"})
        return jsonify({"ok": False, "msg": "不在队列中"})

    # ========== 工具检测 API ==========
    @app.route("/api/git/detect", methods=["GET"])
    def api_git_detect():
        path = svc.get_git_path()
        ver = _tool_version_cached("git:" + str(path), [path, "--version"])
        found = ver is not None
        if not found and not config.IS_WINDOWS and path == "git":
            try:
                rr = subprocess.run(["which", "git"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                if rr.returncode == 0:
                    path = rr.stdout.strip()
                    ver = _tool_version_cached("git:" + str(path), [path, "--version"])
                    found = ver is not None
            except Exception:
                pass
        return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

    @app.route("/api/python/detect", methods=["GET"])
    def api_python_detect():
        path = svc.get_python_path()
        ver = _tool_version_cached("py:" + str(path), [path, "--version"])
        found = ver is not None
        if not found and not config.IS_WINDOWS and path == "python3":
            for alt in ["python3", "python"]:
                try:
                    rr = subprocess.run(["which", alt], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                    if rr.returncode == 0:
                        path = rr.stdout.strip()
                        ver = _tool_version_cached("py:" + str(path), [path, "--version"])
                        if ver is not None:
                            found = True
                            break
                except Exception:
                    pass
        return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

    # ========== 设置 API ==========
    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        data = dict(config.settings)
        rt = dict(getattr(config, "RUNTIME_INFO", None) or {})
        eff_host = str(rt.get("host") or "").strip()
        try:
            eff_port = int(rt.get("port") or 0)
        except (ValueError, TypeError):
            eff_port = 0
        # _effective = 「本进程启动时真实监听」的值（不是 settings 里的值）。
        # 前端拿它和表单里的值比，才知道是否有改动待重启。
        data["_effective"] = {
            "host": eff_host,
            "port": eff_port,
            "started_at": rt.get("started_at") or 0,
            "token_enabled": bool(rt.get("token_enabled")),
            "lan_ips": list(rt.get("lan_ips") or []),
            "running": bool(eff_host and eff_port),
        }
        data["_client"] = {
            "ip": request.remote_addr or "",
            "host": request.host or "",
            "local": sec.is_loopback(request.remote_addr),
        }
        return jsonify(data)

    @app.route("/api/settings", methods=["POST"])
    def api_save_settings():
        d = request.get_json(silent=True)
        if not d:
            return jsonify({"ok": False, "msg": "无效数据"}), 400
        try:
            d["max_concurrent_downloads"] = max(1, min(10, int(d.get("max_concurrent_downloads", 3))))
            d["max_concurrent_clones"] = max(1, min(5, int(d.get("max_concurrent_clones", 2))))
            d["max_connection_per_server"] = max(1, min(16, int(d.get("max_connection_per_server", 8))))
            d["split"] = max(1, min(16, int(d.get("split", 8))))
            d["connect_timeout"] = max(10, int(d.get("connect_timeout", 60)))
            d["timeout"] = max(10, int(d.get("timeout", 60)))
            d["retry_wait"] = max(1, int(d.get("retry_wait", 3)))
            d["max_tries"] = max(0, int(d.get("max_tries", 0)))
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "msg": "参数错误: " + str(e)}), 400
        if not d.get("python_path"):
            d["python_path"] = config.DEFAULT_SETTINGS["python_path"]
        if not d.get("aria2c_path"):
            d["aria2c_path"] = config.DEFAULT_SETTINGS["aria2c_path"]
        if not d.get("comfyui_dir"):
            d["comfyui_dir"] = config.DEFAULT_SETTINGS["comfyui_dir"]
        if not d.get("git_path"):
            d["git_path"] = config.DEFAULT_SETTINGS["git_path"]
        d["check_certificate"] = bool(d.get("check_certificate", False))
        d["auto_start_aria2"] = bool(d.get("auto_start_aria2", True))
        # comfyui 启动参数：字符串直接保留
        for k in ("comfyui_listen", "comfyui_port", "comfyui_vram",
                  "comfyui_preview_method", "comfyui_cors_origin", "comfyui_extra_args"):
            d[k] = str(d.get(k, config.settings.get(k, ""))).strip()
        d["comfyui_auto_launch"] = bool(d.get("comfyui_auto_launch", True))
        # comfyui 环境变量预设
        raw_presets = d.get("comfyui_presets", config.settings.get("comfyui_presets", []))
        if isinstance(raw_presets, list):
            d["comfyui_presets"] = [
                {"key": (p.get("key", "") or "").strip(), "value": (p.get("value", "") or "").strip()}
                for p in raw_presets if isinstance(p, dict) and (p.get("key", "") or "").strip()
            ]
        else:
            d["comfyui_presets"] = config.settings.get("comfyui_presets", [])
        # 壁纸毛玻璃效果 clamp 到 0-10 整数（0=无玻璃，1-10=像素）
        try:
            wb = int(round(float(d.get("wallpaper_blur", config.settings.get("wallpaper_blur", 0)))))
            d["wallpaper_blur"] = max(0, min(10, wb))
        except (ValueError, TypeError):
            d["wallpaper_blur"] = 0
        d["wallpaper_path"] = (d.get("wallpaper_path", "") or "").strip()
        # GitHub 镜像：仅接受已知 key，非法值回退官方
        _gm = str(d.get("github_mirror", config.settings.get("github_mirror", "official")) or "official").strip()
        d["github_mirror"] = _gm if _gm in svc._GITHUB_MIRROR_MAP else "official"
        # ---- 管理器访问 ----
        _wh = str(d.get("web_host", config.settings.get("web_host", "0.0.0.0")) or "").strip()
        d["web_host"] = _wh if _wh in ("0.0.0.0", "127.0.0.1") else "0.0.0.0"
        try:
            _wp = int(d.get("web_port", config.settings.get("web_port", 7860)) or 7860)
        except (ValueError, TypeError):
            _wp = 7860
        d["web_port"] = _wp if 1024 <= _wp <= 65535 else 7860
        d["token_required"] = bool(d.get("token_required", True))
        try:
            _mu = float(d.get("max_upload_gb", config.settings.get("max_upload_gb", 8)) or 8)
        except (ValueError, TypeError):
            _mu = 8.0
        d["max_upload_gb"] = max(0.1, min(64.0, _mu))
        # 令牌有专门的「重新生成」入口，这里只做兜底：前端没带就沿用旧值，绝不允许被清空
        _tk = str(d.get("access_token", "") or "").strip()
        d["access_token"] = _tk or (config.settings.get("access_token") or "")
        if not d["access_token"]:
            d["access_token"] = config.new_access_token()
        config.settings.update(d)
        config.save_settings(config.settings)
        rt = getattr(config, "RUNTIME_INFO", None) or {}
        _cur_host = str(rt.get("host") or "").strip()
        try:
            _cur_port = int(rt.get("port") or 0)
        except (ValueError, TypeError):
            _cur_port = 0
        need_restart = bool(_cur_port) and (d["web_host"] != _cur_host
                                           or int(d["web_port"]) != _cur_port)
        msg = "设置已保存"
        if need_restart:
            msg += "；监听地址/端口有改动，需要重启管理器才生效"
        else:
            msg += "，重启 aria2c 生效"
        return jsonify({"ok": True, "msg": msg, "need_restart": need_restart})

    @app.route("/api/settings/reset", methods=["POST"])
    def api_reset_settings():
        # 访问令牌要保留：重置设置不应该把已经开启的访问保护「悄悄关掉」
        keep_tok = str(config.settings.get("access_token") or "").strip()
        # ⚠️ 必须【原地】清空再填充，不能写成 config.settings = dict(DEFAULT_SETTINGS)：
        # services.py 是 `from .config import settings`（import 时就绑定的同一个 dict 对象），
        # 重新赋值只换掉 config 这边的引用，services 仍然指向旧字典 —— 于是
        # 「恢复默认设置」在界面上看着生效了，后端却还在用旧的 comfyui_dir / python_path /
        # 镜像 / 并发，直到重启。实测：重新赋值后 services.get_comfyui_dir() 仍是旧值。
        # （单用户管理动作，这里不做并发保护；读取方都是 .get(k, 默认)，短窗口不会崩。）
        fresh = dict(config.DEFAULT_SETTINGS)
        fresh["access_token"] = keep_tok or config.new_access_token()
        config.settings.clear()
        config.settings.update(fresh)
        config.save_settings(config.settings)
        return jsonify({"ok": True, "msg": "已恢复默认设置（访问令牌已保留）"})

    @app.route("/api/settings/regenerate_token", methods=["POST"])
    def api_regenerate_token():
        """重新生成访问令牌，立即生效（旧令牌与其 Cookie 立刻失效）"""
        tok = config.new_access_token()
        config.settings["access_token"] = tok
        config.save_settings(config.settings)
        try:
            sec._LOG.warning("访问令牌已重新生成")
        except Exception:
            pass
        return jsonify({"ok": True, "access_token": tok,
                        "msg": "已生成新的访问令牌并立即生效，已登录的其它设备需要重新输入"})

    @app.route("/api/system/quit", methods=["POST"])
    def api_system_quit():
        """退出管理器（只退出，不自动拉起新进程）。
        说明：Windows 下 Werkzeug 开了 SO_REUSEADDR，新进程会和旧进程同时 LISTEN 同一端口，
        先绑定者吃流量 —— 所以这里不做「一键重启」，只做干净退出，由用户重新双击启动器。"""
        def _die():
            time.sleep(0.8)
            try:
                svc.cleanup_all()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=_die, daemon=True).start()
        return jsonify({"ok": True, "msg": "管理器正在退出，请重新双击启动器打开"})

    @app.route("/api/wallpaper")
    def api_wallpaper():
        """提供背景壁纸文件（图片或 mp4）。路径由用户在全局设置中配置。"""
        wp = config.settings.get("wallpaper_path", "").strip()
        if not wp or not os.path.isfile(wp):
            return jsonify({"ok": False, "msg": "壁纸不存在"}), 404
        return send_file(wp)

    # ========== GitHub 镜像 API ==========
    @app.route("/api/github/mirrors")
    def api_github_mirrors():
        """返回可选的 GitHub 镜像列表与当前选择"""
        return jsonify({"ok": True, "mirrors": svc.GITHUB_MIRRORS,
                        "current": config.settings.get("github_mirror", "official")})

    @app.route("/api/github/mirror_test")
    def api_github_mirror_test():
        """并发测试各 GitHub 镜像连通性（探测 git-upload-pack 端点）"""
        return jsonify(svc.test_github_mirrors())

    # ========== ComfyUI 启动器 API ==========
    @app.route("/api/comfyui/start", methods=["POST"])
    def api_comfyui_start():
        s = config.settings  # 实时读取
        python_path = s.get("python_path") or config.DEFAULT_SETTINGS["python_path"]
        comfyui_dir = s.get("comfyui_dir") or config.DEFAULT_SETTINGS["comfyui_dir"]
        return jsonify(svc.start_comfyui(python_path, comfyui_dir))

    @app.route("/api/comfyui/stop", methods=["POST"])
    def api_comfyui_stop():
        return jsonify(svc.stop_comfyui())

    @app.route("/api/comfyui/status")
    def api_comfyui_status():
        return jsonify(svc.get_comfyui_status())

    @app.route("/api/comfyui/logs")
    def api_comfyui_logs():
        since = request.args.get("since", 0, type=int)
        return jsonify(svc.get_comfyui_logs(since))

    @app.route("/api/comfyui/cmd_preview")
    def api_comfyui_cmd_preview():
        s = config.settings
        py = s.get("python_path") or config.DEFAULT_SETTINGS["python_path"]
        d = s.get("comfyui_dir") or config.DEFAULT_SETTINGS["comfyui_dir"]
        cmd = svc.build_comfyui_cmd(py, d)
        prefix = svc._build_preset_env_prefix()
        cmd_str = (prefix or "") + " ".join(cmd)
        return jsonify({"cmd": cmd, "cmd_str": cmd_str})

    @app.route("/api/comfyui/cmd_reset", methods=["POST"])
    def api_comfyui_cmd_reset():
        config.settings["comfyui_listen"] = config.DEFAULT_SETTINGS["comfyui_listen"]
        config.settings["comfyui_port"] = config.DEFAULT_SETTINGS["comfyui_port"]
        config.settings["comfyui_auto_launch"] = config.DEFAULT_SETTINGS["comfyui_auto_launch"]
        config.settings["comfyui_vram"] = config.DEFAULT_SETTINGS["comfyui_vram"]
        config.settings["comfyui_preview_method"] = config.DEFAULT_SETTINGS["comfyui_preview_method"]
        config.settings["comfyui_cors_origin"] = config.DEFAULT_SETTINGS["comfyui_cors_origin"]
        config.settings["comfyui_extra_args"] = config.DEFAULT_SETTINGS["comfyui_extra_args"]
        config.settings["comfyui_presets"] = config.DEFAULT_SETTINGS["comfyui_presets"]
        config.save_settings(config.settings)
        return jsonify({"ok": True, "msg": "ComfyUI 启动命令已重置为默认"})

    # ---- 修复安装 ----

    @app.route("/api/repair/pytorch_options")
    def api_repair_pytorch_options():
        path = os.path.join(config.PACKAGE_DIR, "static", "info", "pytorch.txt")
        return jsonify(svc.parse_pytorch_info(path))

    @app.route("/api/repair/clone", methods=["POST"])
    def api_repair_clone():
        d = request.get_json(force=True) or {}
        git_path = config.settings.get("git_path", "git")
        target_dir = (d.get("target_dir") or "").strip()
        if not target_dir:
            return jsonify({"ok": False, "msg": "请指定安装目录"})
        # 镜像统一取自全局设置中的 GitHub 镜像
        return jsonify(svc.repair_clone(git_path, target_dir))

    @app.route("/api/repair/clone_check")
    def api_repair_clone_check():
        d = request.args.get("dir", "")
        return jsonify({"exists": bool(d and os.path.exists(os.path.join(d, "main.py")))})

    @app.route("/api/repair/comfyui_branches")
    def api_repair_comfyui_branches():
        """分支 / tag 列表。

        ⚠️ 这里**不能**同步跑 git fetch —— 慢网络下几十秒，用户一切到本页就是个卡住的
        下拉框。服务层现在立即返回本地 ref 快照，远端刷新在后台线程里跑；响应体
        refreshing=True 时前端继续轮询本地址。?refresh=1 强制提交一次远端刷新。
        """
        git_path = config.settings.get("git_path", "git")
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        force = request.args.get("refresh") in ("1", "true", "yes")
        return jsonify(svc.get_comfyui_branches(git_path, comfyui_dir, force=force))

    @app.route("/api/repair/comfyui_update", methods=["POST"])
    def api_repair_comfyui_update():
        d = request.get_json(force=True) or {}
        git_path = config.settings.get("git_path", "git")
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        target = (d.get("target") or "").strip()
        # 先等后台的分支刷新收工：git fetch 与 git pull 同时写 refs 会抢锁，
        # 让用户在日志里看到 "cannot lock ref" 是很差的体验。最多等 12 秒。
        svc.comfyui_git_idle()
        return jsonify(svc.repair_comfyui_update(git_path, comfyui_dir, target))

    @app.route("/api/repair/requirements", methods=["POST"])
    def api_repair_requirements():
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        return jsonify(svc.repair_requirements(python_path, comfyui_dir))

    @app.route("/api/repair/mirror_info")
    def api_repair_mirror_info():
        """当前生效的 GitHub / pip 镜像信息（修复区域展示用）"""
        return jsonify(svc.repair_mirror_info())

    @app.route("/api/repair/pytorch", methods=["POST"])
    def api_repair_pytorch():
        d = request.get_json(force=True) or {}
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        cmd_str = (d.get("cmd") or "").strip()
        return jsonify(svc.repair_pytorch(python_path, cmd_str))

    @app.route("/api/repair/stop", methods=["POST"])
    def api_repair_stop():
        return jsonify(svc.repair_stop())

    @app.route("/api/repair/purge_cache", methods=["POST"])
    def api_repair_purge_cache():
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        return jsonify(svc.repair_purge_cache(python_path))

    @app.route("/api/repair/status")
    def api_repair_status():
        return jsonify(svc.repair_status())

    @app.route("/api/repair/logs")
    def api_repair_logs():
        since = request.args.get("since", 0, type=int)
        return jsonify(svc.repair_logs_get(since))

    @app.route("/api/repair/plugin", methods=["POST"])
    def api_repair_plugin():
        d = request.get_json(force=True) or {}
        git_path = config.settings.get("git_path", "git")
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        plugin_name = (d.get("plugin_name") or "").strip()
        if not plugin_name:
            return jsonify({"ok": False, "msg": "请填写插件名称"})
        # 镜像统一取自全局设置（GitHub 镜像 + pip 镜像源）
        return jsonify(svc.repair_single_plugin(git_path, python_path, comfyui_dir, plugin_name))

    @app.route("/api/repair/pip_install", methods=["POST"])
    def api_repair_pip_install():
        d = request.get_json(force=True) or {}
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        packages = (d.get("packages") or "").strip()
        # 镜像统一取自全局设置中的 pip 镜像源
        return jsonify(svc.repair_pip_install(python_path, packages))

    @app.route("/api/repair/download", methods=["POST"])
    def api_repair_download():
        d = request.get_json(force=True) or {}
        aria2c = config.settings.get("aria2c_path", config.DEFAULT_SETTINGS["aria2c_path"])
        url = (d.get("url") or "").strip()
        save_path = (d.get("save_path") or "").strip()
        filename = (d.get("filename") or "").strip()
        return jsonify(svc.repair_download_file(aria2c, url, save_path, filename))

    @app.route("/api/repair/all_plugins", methods=["POST"])
    def api_repair_all_plugins():
        d = request.get_json(silent=True) or {}
        git_path = config.settings.get("git_path", "git")
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        # 镜像统一取自全局设置（GitHub 镜像 + pip 镜像源）
        return jsonify(svc.repair_all_plugins(git_path, python_path, comfyui_dir))

    @app.route("/api/repair/kill_comfyui", methods=["POST"])
    def api_repair_kill_comfyui():
        return jsonify(svc.kill_comfyui_processes())

    @app.route("/api/repair/kill_aria2", methods=["POST"])
    def api_repair_kill_aria2():
        return jsonify(svc.kill_aria2_processes())

    # ========== 输出管理 API ==========
    def _get_output_dir():
        d = config.settings.get("output_dir", "").strip()
        if d:
            return d
        comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
        return os.path.join(comfy, "output")

    @app.route("/api/output/browse")
    def api_output_browse():
        root = _get_output_dir()
        rel = request.args.get("path", "")
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        search = request.args.get("search", "")
        sort = request.args.get("sort", "date_desc")
        return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

    @app.route("/api/output/file")
    def api_output_file():
        root = _get_output_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_output_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path)

    @app.route("/api/output/download")
    def api_output_download():
        """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
        root = _get_output_dir()
        paths = request.args.get("paths", "")
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        rel_paths = [p for p in paths.split(",") if p]
        if not rel_paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        # 单个文件：直接附件下载
        if len(rel_paths) == 1:
            fp = svc.serve_output_file(root, rel_paths[0])
            # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
            # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
            if fp and os.path.isfile(fp):
                return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
        # 多选/文件夹：打包 zip
        items, gen = svc.zip_files_stream(root, rel_paths, "output.zip")
        if not items:
            return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
        return _zip_response(gen, "output.zip")

    @app.route("/api/output/open_dir")
    def api_output_open_dir():
        """在资源管理器中打开输出目录"""
        d = _get_output_dir()
        if os.path.isdir(d):
            try:
                # explorer <path> 比 os.startfile 更可靠地前置窗口
                subprocess.Popen(['explorer', d], shell=False)
            except Exception:
                os.startfile(d)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "目录不存在: " + d})

    @app.route("/api/output/delete", methods=["POST"])
    def api_output_delete():
        root = _get_output_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"})
        return jsonify(svc.delete_output_files(root, paths))

    @app.route("/api/output/rename", methods=["POST"])
    def api_output_rename():
        root = _get_output_dir()
        data = request.get_json(silent=True) or {}
        path = data.get("path", "")
        new_name = data.get("new_name", "")
        if not path or not new_name:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.rename_output_file(root, path, new_name))

    @app.route("/api/output/move", methods=["POST"])
    def api_output_move():
        root = _get_output_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.move_output_files(root, paths, dest))

    @app.route("/api/output/copy", methods=["POST"])
    def api_output_copy():
        root = _get_output_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.copy_output_files(root, paths, dest))

    @app.route("/api/output/send_to_input", methods=["POST"])
    def api_output_send_to_input():
        """将选中的输出文件发送到输入文件夹"""
        root_out = _get_output_dir()
        root_in = _get_input_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"})
        return jsonify(svc.send_to_input_dir(root_out, paths, root_in))

    @app.route("/api/output/mkdir", methods=["POST"])
    def api_output_mkdir():
        root = _get_output_dir()
        data = request.get_json(silent=True) or {}
        path = data.get("path", "")
        if not path:
            return jsonify({"ok": False, "msg": "缺少路径"})
        return jsonify(svc.create_output_dir(root, path))

    @app.route("/api/output/upload", methods=["POST"])
    def api_output_upload():
        root = _get_output_dir()
        dest = (request.form.get("dest") or "").strip()
        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "msg": "未选择文件"})
        return jsonify(svc.upload_output_files(root, dest, files,
                                               request.form.getlist("paths")))


    # ========== 输入管理 API ==========
    def _get_input_dir():
        d = config.settings.get("input_dir", "").strip()
        if d:
            return d
        comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
        return os.path.join(comfy, "input")

    @app.route("/api/input/browse")
    def api_input_browse():
        root = _get_input_dir()
        rel = request.args.get("path", "")
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        search = request.args.get("search", "")
        sort = request.args.get("sort", "date_desc")
        return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

    @app.route("/api/input/file")
    def api_input_file():
        root = _get_input_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_output_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path)

    @app.route("/api/input/download")
    def api_input_download():
        """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
        root = _get_input_dir()
        paths = request.args.get("paths", "")
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        rel_paths = [p for p in paths.split(",") if p]
        if not rel_paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        if len(rel_paths) == 1:
            fp = svc.serve_output_file(root, rel_paths[0])
            # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
            # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
            if fp and os.path.isfile(fp):
                return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
        items, gen = svc.zip_files_stream(root, rel_paths, "input.zip")
        if not items:
            return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
        return _zip_response(gen, "input.zip")

    @app.route("/api/input/open_dir")
    def api_input_open_dir():
        d = _get_input_dir()
        if os.path.isdir(d):
            try:
                subprocess.Popen(['explorer', d], shell=False)
            except Exception:
                os.startfile(d)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "目录不存在: " + d})

    @app.route("/api/input/delete", methods=["POST"])
    def api_input_delete():
        root = _get_input_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"})
        return jsonify(svc.delete_output_files(root, paths))

    @app.route("/api/input/rename", methods=["POST"])
    def api_input_rename():
        root = _get_input_dir()
        data = request.get_json(silent=True) or {}
        path = data.get("path", "")
        new_name = data.get("new_name", "")
        if not path or not new_name:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.rename_output_file(root, path, new_name))

    @app.route("/api/input/move", methods=["POST"])
    def api_input_move():
        root = _get_input_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.move_output_files(root, paths, dest))

    @app.route("/api/input/copy", methods=["POST"])
    def api_input_copy():
        root = _get_input_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.copy_output_files(root, paths, dest))

    @app.route("/api/input/mkdir", methods=["POST"])
    def api_input_mkdir():
        root = _get_input_dir()
        data = request.get_json(silent=True) or {}
        path = data.get("path", "")
        if not path:
            return jsonify({"ok": False, "msg": "缺少路径"})
        return jsonify(svc.create_output_dir(root, path))

    @app.route("/api/input/upload", methods=["POST"])
    def api_input_upload():
        root = _get_input_dir()
        dest = (request.form.get("dest") or "").strip()
        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "msg": "未选择文件"})
        return jsonify(svc.upload_output_files(root, dest, files,
                                               request.form.getlist("paths")))

    # ========== 工作流管理 API ==========
    def _get_workflow_dir():
        d = config.settings.get("workflow_dir", "").strip()
        if d:
            return d
        comfy = config.settings.get("comfyui_dir", config.DEFAULT_SETTINGS["comfyui_dir"])
        return os.path.join(comfy, "user", "default", "workflows")

    @app.route("/api/workflows/browse")
    def api_workflows_browse():
        root = _get_workflow_dir()
        rel = request.args.get("path", "")
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
        search = request.args.get("search", "")
        sort = request.args.get("sort", "date_desc")
        return jsonify(svc.workflows_browse(root, rel, page, page_size, search, sort))

    @app.route("/api/workflows/get")
    def api_workflows_get():
        root = _get_workflow_dir()
        rel = request.args.get("path", "")
        return jsonify(svc.workflows_get(root, rel))

    @app.route("/api/workflows/save", methods=["POST"])
    def api_workflows_save():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        rel = data.get("path", "")
        content = data.get("content", "")
        if not rel:
            return jsonify({"ok": False, "msg": "缺少路径"})
        return jsonify(svc.workflows_save(root, rel, content))

    @app.route("/api/workflows/new", methods=["POST"])
    def api_workflows_new():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        name = data.get("name", "")
        if not name:
            return jsonify({"ok": False, "msg": "缺少文件名"})
        return jsonify(svc.workflows_new(root, name))

    @app.route("/api/workflows/delete", methods=["POST"])
    def api_workflows_delete():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"})
        return jsonify(svc.delete_output_files(root, paths))

    @app.route("/api/workflows/rename", methods=["POST"])
    def api_workflows_rename():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        rel = data.get("path", "")
        new_name = data.get("new_name", "")
        if not rel or not new_name:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.rename_output_file(root, rel, new_name))

    @app.route("/api/workflows/move", methods=["POST"])
    def api_workflows_move():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.workflows_move_files(root, paths, dest))

    @app.route("/api/workflows/copy", methods=["POST"])
    def api_workflows_copy():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        paths = data.get("paths", [])
        dest = data.get("dest", "")
        if not paths or dest is None:
            return jsonify({"ok": False, "msg": "缺少参数"})
        return jsonify(svc.workflows_copy_files(root, paths, dest))

    @app.route("/api/workflows/mkdir", methods=["POST"])
    def api_workflows_mkdir():
        root = _get_workflow_dir()
        data = request.get_json(silent=True) or {}
        path = data.get("path", "")
        if not path:
            return jsonify({"ok": False, "msg": "缺少路径"})
        return jsonify(svc.create_output_dir(root, path))

    @app.route("/api/workflows/upload", methods=["POST"])
    def api_workflows_upload():
        """上传单个或多个文件/整个文件夹到工作流目录（multipart：files, dest, paths[]）"""
        root = _get_workflow_dir()
        dest = (request.form.get("dest") or "").strip()
        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "msg": "未选择文件"})
        return jsonify(svc.upload_output_files(root, dest, files,
                                               request.form.getlist("paths")))

    @app.route("/api/workflows/file")
    def api_workflows_file():
        root = _get_workflow_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_workflow_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path, as_attachment=True)

    @app.route("/api/workflows/download")
    def api_workflows_download():
        """下载单个文件（附件）或多个文件/文件夹（打包 zip）"""
        root = _get_workflow_dir()
        paths = request.args.get("paths", "")
        if not paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        rel_paths = [p for p in paths.split(",") if p]
        if not rel_paths:
            return jsonify({"ok": False, "msg": "未指定路径"}), 400
        if len(rel_paths) == 1:
            fp = svc.serve_workflow_file(root, rel_paths[0])
            # ⚠️ 一个路径不一定是文件 —— 右键单个文件夹点「下载」时同样只发 1 个路径，
            # 而 serve_*_file() 对目录返回 None。只有确认是文件才直下，否则落到打包分支。
            if fp and os.path.isfile(fp):
                return send_file(fp, as_attachment=True, download_name=os.path.basename(fp))
        items, gen = svc.zip_files_stream(root, rel_paths, "workflows.zip")
        if not items:
            return jsonify({"ok": False, "msg": "无有效文件可下载"}), 404
        return _zip_response(gen, "workflows.zip")

    @app.route("/api/workflows/backup", methods=["POST"])
    def api_workflows_backup():
        root = _get_workflow_dir()
        backup_dir = config.settings.get("workflow_backup_dir", "").strip()
        if not backup_dir:
            return jsonify({"ok": False, "msg": "未配置备份目录，请在全局设置中填写"})
        return jsonify(svc.workflows_backup(root, backup_dir))

    @app.route("/api/workflows/open_dir")
    def api_workflows_open_dir():
        d = _get_workflow_dir()
        if os.path.isdir(d):
            try:
                subprocess.Popen(['explorer', d], shell=False)
            except Exception:
                os.startfile(d)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "目录不存在: " + d})


    @app.route("/api/workflows/analyze")
    def api_workflows_analyze():
        root = _get_workflow_dir()
        rel = request.args.get("path", "")
        if not rel:
            return jsonify({"ok": False, "msg": "缺少路径"})
        return jsonify(svc.workflows_analyze(root, rel))

    # ========== 笔记本 API ==========
    db.notes_init_db()

    @app.route("/api/notes", methods=["GET"])
    def api_notes_list():
        kw = request.args.get("q", "").strip()
        tag = request.args.get("tag", "").strip()
        sort = request.args.get("sort", "updated_desc").strip()
        items = db.notes_get_all(kw, tag, sort)
        return jsonify({"ok": True, "items": items, "tags": db.notes_get_tags()})

    @app.route("/api/notes", methods=["POST"])
    def api_notes_add():
        d = request.get_json(force=True, silent=True) or {}
        title = (d.get("title") or "未命名笔记").strip()[:200]
        content = d.get("content") or ""
        color = d.get("color") or "#00d4ff"
        tag = (d.get("tag") or "").strip()[:50]
        rid = db.notes_add(title, content, color, tag)
        return jsonify({"ok": True, "id": rid})

    @app.route("/api/notes/<int:rid>", methods=["GET"])
    def api_notes_get(rid):
        row = db.notes_get(rid)
        return jsonify({"ok": True, "note": row}) if row else (jsonify({"ok": False, "msg": "笔记不存在"}), 404)

    @app.route("/api/notes/<int:rid>", methods=["PUT"])
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

    @app.route("/api/notes/<int:rid>", methods=["DELETE"])
    def api_notes_delete(rid):
        db.notes_delete(rid)
        return jsonify({"ok": True})

    @app.route("/api/notes/<int:rid>/pin", methods=["POST"])
    def api_notes_pin(rid):
        db.notes_toggle_pin(rid)
        return jsonify({"ok": True})

    # ========== 关机 API ==========
    @app.route("/api/shutdown", methods=["POST"])
    def api_shutdown():
        d = request.get_json(silent=True) or {}
        # 延迟的取值与校验统一放在 services.schedule_shutdown 里，
        # 避免这里再兜一层 int() 把非法值悄悄改成 60（会无视用户配置）。
        delay = d.get("delay", config.settings.get("shutdown_delay", 60))
        return jsonify(svc.schedule_shutdown(delay))

    @app.route("/api/shutdown/cancel", methods=["POST"])
    def api_shutdown_cancel():
        return jsonify(svc.cancel_shutdown())

    @app.route("/api/shutdown/status")
    def api_shutdown_status():
        return jsonify(svc.shutdown_status())
