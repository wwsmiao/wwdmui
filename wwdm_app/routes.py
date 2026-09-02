import os, subprocess, platform as _platform
from flask import request, jsonify, render_template, send_file
from . import config
from . import database as db
from . import services as svc


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
        ver = None
        if found:
            try:
                rr = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                ver = rr.stdout.split("\n")[0] if rr.stdout else ""
            except Exception:
                pass
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

        if installed_only:
            # 已安装过滤：全量取出再内存过滤分页，避免 DB 分页截断
            plist = db.plugin_search(kw, sort=sort, limit=99999, offset=0)
            for p in plist:
                p["installed"] = p["name"] in installed
            plist = [p for p in plist if p["installed"]]
            total = len(plist)
            # 内存分页
            offset = (page - 1) * page_size
            plist = plist[offset:offset + page_size]
        else:
            offset = (page - 1) * page_size
            plist = db.plugin_search(kw, sort=sort, limit=page_size, offset=offset)
            for p in plist:
                p["installed"] = p["name"] in installed
            total = db.plugin_count(kw)

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
        ok, msg = svc.plugin_uninstall(p["name"])
        if not ok:
            return jsonify({"ok": False, "error": msg}), 500
        db.plugin_delete(pid)
        return jsonify({"ok": True, "msg": msg})

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
        d = request.get_json(silent=True) or {}
        ok, msg = svc.start_clone(pid, use_mirror=bool(d.get("mirror")))
        return jsonify({"ok": ok, "msg": msg})

    @app.route("/api/plugins/clone_batch", methods=["POST"])
    def api_clone_batch():
        d = request.get_json(silent=True) or {}
        pids = d.get("ids", [])
        if not isinstance(pids, list) or not pids:
            return jsonify({"ok": False, "msg": "ids无效"}), 400
        return jsonify({"ok": True, "results": svc.start_clone_batch(pids, bool(d.get("mirror")))})

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
        found = False
        ver = None
        try:
            r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
            if r.returncode == 0:
                found = True
                ver = r.stdout.split("\n")[0] if r.stdout else ""
        except Exception:
            if not config.IS_WINDOWS and path == "git":
                try:
                    rr = subprocess.run(["which", "git"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                    if rr.returncode == 0:
                        path = rr.stdout.strip()
                        found = True
                        rv = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                        ver = rv.stdout.split("\n")[0] if rv.stdout else ""
                except Exception:
                    pass
        return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

    @app.route("/api/python/detect", methods=["GET"])
    def api_python_detect():
        path = svc.get_python_path()
        found = False
        ver = None
        try:
            r = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
            if r.returncode == 0:
                found = True
                ver = (r.stdout or r.stderr).split("\n")[0].strip()
        except Exception:
            pass
        if not found and not config.IS_WINDOWS and path == "python3":
            for alt in ["python3", "python"]:
                try:
                    rr = subprocess.run(["which", alt], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                    if rr.returncode == 0:
                        path = rr.stdout.strip()
                        rv = subprocess.run([path, "--version"], capture_output=True, text=True, encoding="gbk", errors="replace", timeout=5)
                        ver = (rv.stdout or rv.stderr).split("\n")[0].strip()
                        found = True
                        break
                except Exception:
                    pass
        return jsonify({"found": found, "path": path, "version": ver, "platform": _platform.system()})

    # ========== 设置 API ==========
    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        return jsonify(config.settings)

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
        config.settings.update(d)
        config.save_settings(config.settings)
        return jsonify({"ok": True, "msg": "设置已保存，重启 aria2c 生效"})

    @app.route("/api/settings/reset", methods=["POST"])
    def api_reset_settings():
        config.settings = dict(config.DEFAULT_SETTINGS)
        config.save_settings(config.settings)
        return jsonify({"ok": True, "msg": "已恢复默认设置"})

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
        mirror = bool(d.get("mirror", False))
        target_dir = (d.get("target_dir") or "").strip()
        if not target_dir:
            return jsonify({"ok": False, "msg": "请指定安装目录"})
        return jsonify(svc.repair_clone(git_path, mirror, target_dir))

    @app.route("/api/repair/clone_check")
    def api_repair_clone_check():
        d = request.args.get("dir", "")
        return jsonify({"exists": bool(d and os.path.exists(os.path.join(d, "main.py")))})

    @app.route("/api/repair/comfyui_branches")
    def api_repair_comfyui_branches():
        git_path = config.settings.get("git_path", "git")
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        return jsonify(svc.get_comfyui_branches(git_path, comfyui_dir))

    @app.route("/api/repair/comfyui_update", methods=["POST"])
    def api_repair_comfyui_update():
        d = request.get_json(force=True) or {}
        git_path = config.settings.get("git_path", "git")
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        target = (d.get("target") or "").strip()
        return jsonify(svc.repair_comfyui_update(git_path, comfyui_dir, target))

    @app.route("/api/repair/requirements", methods=["POST"])
    def api_repair_requirements():
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        return jsonify(svc.repair_requirements(python_path, comfyui_dir))

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
        mirror = d.get("mirror") or None
        return jsonify(svc.repair_single_plugin(git_path, python_path, comfyui_dir, plugin_name, mirror))

    @app.route("/api/repair/pip_install", methods=["POST"])
    def api_repair_pip_install():
        d = request.get_json(force=True) or {}
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        packages = (d.get("packages") or "").strip()
        mirror = (d.get("mirror") or "official").strip()
        return jsonify(svc.repair_pip_install(python_path, packages, mirror))

    @app.route("/api/repair/download", methods=["POST"])
    def api_repair_download():
        d = request.get_json(force=True) or {}
        aria2c = config.settings.get("aria2c_path", config.DEFAULT_SETTINGS["aria2c_path"])
        url = (d.get("url") or "").strip()
        save_path = (d.get("save_path") or "").strip()
        return jsonify(svc.repair_download_file(aria2c, url, save_path))

    @app.route("/api/repair/all_plugins", methods=["POST"])
    def api_repair_all_plugins():
        d = request.get_json(silent=True) or {}
        git_path = config.settings.get("git_path", "git")
        python_path = config.settings.get("python_path", config.DEFAULT_SETTINGS["python_path"])
        comfyui_dir = config.settings.get("comfyui_dir", config.COMFYUI_DIR)
        mirror = d.get("mirror") or None  # None=官方直连, str=镜像前缀
        return jsonify(svc.repair_all_plugins(git_path, python_path, comfyui_dir, mirror))

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
        sort = request.args.get("sort", "name_asc")
        return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

    @app.route("/api/output/file")
    def api_output_file():
        root = _get_output_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_output_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path)

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
        return jsonify(svc.upload_output_files(root, dest, files))



    # ========== 抖音提取 API ==========
    def _douyin_cookie_file():
        """获取抖音 Cookie 文件路径"""
        f = config.settings.get("douyin_cookie_file", "").strip()
        if f and os.path.isfile(f):
            return f
        # fallback: 项目目录下 dy_cookie.json
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dy_cookie.json")
        if os.path.isfile(fallback):
            return fallback
        return None

    @app.route("/api/douyin/fetch", methods=["POST"])
    def api_douyin_fetch():
        """提取抖音链接的图片/视频信息（使用 dy_cookie.json）"""
        data = request.get_json(silent=True) or {}
        raw_text = (data.get("url") or data.get("text") or "").strip()
        if not raw_text:
            return jsonify({"ok": False, "msg": "请输入抖音链接或包含链接的文本"})
        cookie_file = _douyin_cookie_file()
        if not cookie_file:
            return jsonify({"ok": False, "msg": "请先在全局设置中配置 dy_cookie.json 路径（Cookie 文件格式：原始 Cookie 字符串或 JSON）"})
        return jsonify(svc.douyin_fetch(raw_text, cookie_file))

    @app.route("/api/douyin/download", methods=["POST"])
    def api_douyin_download():
        """通过 aria2c 下载图片或视频"""
        data = request.get_json(silent=True) or {}
        save_dir = (data.get("save_dir") or "").strip()
        if not save_dir:
            save_dir = config.settings.get("douyin_save_dir", "").strip()
        if not save_dir:
            return jsonify({"ok": False, "msg": "请配置保存目录"})

        # 视频下载
        video_url = (data.get("video_url") or "").strip()
        if video_url:
            name = (data.get("video_name") or data.get("aweme_id") or "video").strip() + ".mp4"
            cookie_file = _douyin_cookie_file()
            return jsonify(svc.douyin_download_video(video_url, save_dir, name, cookie_file))

        # 图片下载
        urls = data.get("urls", [])
        if not urls:
            return jsonify({"ok": False, "msg": "没有可下载的图片"})
        tmpl = (data.get("name_template") or "{index:02d}.{ext}").strip()
        cookie_file = _douyin_cookie_file()
        return jsonify(svc.douyin_download_images(urls, save_dir, tmpl, cookie_file))


    @app.route("/api/douyin/progress/<gid>")
    def api_douyin_progress(gid):
        return jsonify(svc.douyin_download_status(gid))


    @app.route("/api/douyin/open_dir")
    def api_douyin_open_dir():
        d = config.settings.get("douyin_save_dir", "").strip()
        if d and os.path.isdir(d):
            try:
                subprocess.Popen(['explorer', d], shell=False)
            except Exception:
                os.startfile(d)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "目录不存在或未配置"})
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
        sort = request.args.get("sort", "name_asc")
        return jsonify(svc.browse_output_dir(root, rel, page, page_size, search, sort))

    @app.route("/api/input/file")
    def api_input_file():
        root = _get_input_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_output_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path)

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
        return jsonify(svc.upload_output_files(root, dest, files))

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
        sort = request.args.get("sort", "name_asc")
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

    @app.route("/api/workflows/file")
    def api_workflows_file():
        root = _get_workflow_dir()
        rel = request.args.get("path", "")
        file_path = svc.serve_workflow_file(root, rel)
        if not file_path:
            return jsonify({"ok": False, "msg": "文件不存在或路径无效"}), 404
        return send_file(file_path, as_attachment=True)

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
        try:
            delay = int(d.get("delay", config.settings.get("shutdown_delay", 60)))
        except (TypeError, ValueError):
            delay = 60
        return jsonify(svc.schedule_shutdown(delay))

    @app.route("/api/shutdown/cancel", methods=["POST"])
    def api_shutdown_cancel():
        return jsonify(svc.cancel_shutdown())
