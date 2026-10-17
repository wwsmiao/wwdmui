# -*- coding: utf-8 -*-
"""插件库 API 蓝图（v5.0 第 6 步 域 4/10）。

只放路由，不含业务逻辑；业务实现见 services/plugins.py。
克隆相关路由（/api/plugins/clone*）暂留 routes.py，等 clone 域拆出时一并搬走。
"""
import os
import subprocess

from flask import Blueprint, jsonify, request

from .. import database as db
from .. import services as svc

bp = Blueprint("plugins", __name__)


# ========== 插件 API ==========
@bp.route("/api/plugins", methods=["GET"])
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

@bp.route("/api/plugins", methods=["POST"])
def api_plugin_add():
    d = request.get_json(force=True, silent=True)
    if not d or not d.get("name") or not d.get("url"):
        return jsonify({"ok": False, "msg": "参数不完整"}), 400
    pid = db.plugin_add(d["name"].strip(), d["url"].strip(), d.get("description", "").strip())
    return jsonify({"ok": True, "id": pid})

@bp.route("/api/plugins/<int:pid>", methods=["GET"])
def api_plugin_get(pid):
    p = db.plugin_get(pid)
    if not p:
        return jsonify({"ok": False, "msg": "未找到"}), 404
    installed = db.plugin_get_names()
    p["installed"] = p["name"] in installed
    p["repo_url"] = p.get("url", "")
    p["desc"] = p.get("description", "")
    return jsonify(p)

@bp.route("/api/plugins/<int:pid>", methods=["PUT"])
def api_plugin_update(pid):
    d = request.get_json(force=True, silent=True)
    if not d:
        return jsonify({"ok": False}), 400
    db.plugin_update(pid, d["name"].strip(), d["url"].strip(), d.get("description", "").strip())
    return jsonify({"ok": True})

@bp.route("/api/plugins/<int:pid>", methods=["DELETE"])
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
        svc.audit("plugin.delete", target=p["name"], detail=msg, ok=False)
        return jsonify({"ok": False, "error": msg}), 500
    svc.audit("plugin.delete", target=p["name"], detail="已删除插件目录（数据库记录保留）")
    return jsonify({"ok": True, "msg": msg + "（数据库记录已保留）"})

@bp.route("/api/plugins/installed", methods=["GET"])
def api_plugin_installed():
    return jsonify(db.plugin_get_names())

@bp.route("/api/plugins/open_dir")
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


@bp.route("/api/plugins/repair_db", methods=["POST"])
def api_plugin_repair_db():
    ok, res = db.plugin_repair_db()
    if not ok:
        return jsonify({"ok": False, "msg": res.get("msg", "修复失败")}), 500
    return jsonify({"ok": True, "result": res,
                    "msg": "修复完成：%d → %d 条，去除重复 %d 个" % (
                        res["total_before"], res["total_after"], res["removed_count"])})

@bp.route("/api/plugins/update_library", methods=["POST"])
def api_plugin_update_library():
    """从 ComfyUI-Manager 官方数据源拉取最新插件库，合并写入 models.db

    写库规则：按规范化仓库地址合并 —— 已有的覆盖名称/描述，没有的新增，
    绝不简单追加；同时重建「节点类名 → 插件仓库」映射表（工作流解析用）。
    """
    res = svc.plugins_update_library()
    if not res.get("ok"):
        return jsonify(res), 502
    return jsonify(res)

