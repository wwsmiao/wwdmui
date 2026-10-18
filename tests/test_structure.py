# -*- coding: utf-8 -*-
"""结构约束 —— 把架构规矩固化成断言，防止后人（包括我）顺手破坏。

这些不是功能测试，是「别退回去」护栏：
  * 路由总数恒定、蓝图齐全、routes.py 不许再长胖
  * core/ 不许反向 import services/routes（曾经因此 500 过）
  * 子域再导出必须是同一个对象（可打桩）
  * 模板分层的 block 位置不能乱挪
  * 行尾按文件约定（.gitattributes 钉死）
"""
import ast
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "wwdm_app")

EXPECTED_BLUEPRINTS = {
    "clone", "comfyui", "download", "files", "input", "models",
    "notes", "output", "plugins", "repair", "settings", "shutdown", "tools",
    "workflows", "audit",
}
# 含 Flask /static。⚠️ 同一个 rule 字符串若挂了两个 endpoint（如 /api/tasks 的
# GET 与 DELETE），url_map 里算**两条** Rule —— 所以加了清理端点后是 135 不是 134。
# v5.1 又加了 /api/github/mirror_fastest → 136，/api/audit → 137。
EXPECTED_ROUTES = 136


# ============================================================
# 路由
# ============================================================
class TestRoutes:
    def test_total_route_count(self, app):
        got = len(list(app.url_map.iter_rules()))
        assert got == EXPECTED_ROUTES, "路由总数变了：%d（预期 %d）" % (got, EXPECTED_ROUTES)

    def test_all_blueprints_registered(self, app):
        missing = EXPECTED_BLUEPRINTS - set(app.blueprints)
        assert not missing, "蓝图没注册：%s" % sorted(missing)

    def test_no_unexpected_blueprints(self, app):
        extra = set(app.blueprints) - EXPECTED_BLUEPRINTS
        assert not extra, "多了预期外的蓝图：%s" % sorted(extra)

    def test_routes_py_only_serves_index(self, app):
        """routes.py 只剩首页，新路由一律写进 blueprints/。

        首页是用 ``app.route`` 直接挂的（不在蓝图里），所以端点名就是函数名
        ``index``，不带蓝图前缀。
        """
        home = [r for r in app.url_map.iter_rules() if r.endpoint == "index"]
        assert home, "routes.py 的首页端点没了"
        assert home[0].rule == "/"
        assert "GET" in home[0].methods

    @pytest.mark.parametrize("endpoint", [
        "api_tasks",            # core/tasks.py 直接挂，无蓝图前缀
        "api_tasks_clear",      # 第 11 步补的清理端点
        "models.api_list",
        "settings.api_get_settings",
        "download.api_dl_all_status",
        "plugins.api_plugin_installed",
    ])
    def test_key_endpoints_exist(self, app, endpoint):
        assert endpoint in app.view_functions, "端点缺失：%s" % endpoint

    @pytest.mark.parametrize("bp", sorted(EXPECTED_BLUEPRINTS))
    def test_every_blueprint_has_routes(self, app, bp):
        n = len([r for r in app.url_map.iter_rules()
                 if r.endpoint.startswith(bp + ".")])
        assert n > 0, "蓝图 %s 一条路由都没有" % bp

    def test_task_endpoints_registered(self, app):
        """第 11 步补的清理端点必须在。"""
        paths = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/tasks" in paths
        methods = {m for r in app.url_map.iter_rules()
                   if r.rule == "/api/tasks" for m in r.methods} - {"HEAD", "OPTIONS"}
        assert methods == {"GET", "DELETE"}


# ============================================================
# 依赖方向
# ============================================================
class TestDependencyDirection:
    @pytest.mark.parametrize("name", [
        "config.py", "db.py", "tasks.py", "errors.py", "logging.py",
        "security.py", "__init__.py",
    ])
    def test_core_never_imports_business(self, name):
        """core/ 只许被业务依赖，不许反向 import services/routes。

        踩过：core/db.py 曾 ``from .services import get_comfyui_dir`` →
        搬进 core 后 GET /api/plugins?installed=1 直接 500。
        """
        path = os.path.join(PKG, "core", name)
        if not os.path.exists(path):
            pytest.skip("文件不存在：%s" % name)
        tree = ast.parse(open(path, "rb").read().decode("utf-8"))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if re.search(r"(^|\.)(services|routes|blueprints)($|\.)", mod):
                    bad.append("from %s import ..." % mod)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if re.search(r"(^|\.)(services|routes|blueprints)($|\.)", a.name):
                        bad.append("import %s" % a.name)
        assert not bad, "core/%s 反向依赖了业务层：%s" % (name, bad)

    def test_services_subpackage_reexports_are_same_object(self):
        """子域再导出必须 ``svc.X is services.<域>.X`` —— 否则打桩不生效。

        可变状态（list/dict/Lock）尤其关键：包上别名与域内必须是同一个对象，
        不然域内 append 了包上看不见，打桩也打不中。
        """
        import wwdm_app.services as svc
        from wwdm_app.services import (clone, files, mirror, output, plugins,
                                       shutdown, workflows, auditlog)

        shared = {
            clone: ["clone_procs", "clone_infos", "clone_mirrors", "clone_queue",
                    "clone_lock", "clone_cleanup_lock"],
            files: ["_dir_stats_cache", "_dir_stats_lock"],
            workflows: ["_wf_lock", "_wf_objinfo_cache", "_wf_packmap_cache",
                        "_wf_modelidx_cache"],
            shutdown: ["_SHUTDOWN_STATE", "_shutdown_lock"],
            plugins: ["_PLUGIN_LIB_BASES"],
            output: ["_safe_path", "_safe_rel", "_is_inside", "recursive_search"],
            # v5.1 新拆的镜像域：GitHub 镜像表 + HF 渠道切换
            mirror: ["GITHUB_MIRRORS", "GITHUB_PROBE_URL", "_GITHUB_MIRROR_MAP",
                     "HF_CHANNELS", "HF_ENV_KEY", "convert_to_mirror",
                     "apply_github_mirror", "convert_git_mirror",
                     "github_mirror_prefix", "github_mirror_label",
                     "test_github_mirrors", "pick_fastest_mirror",
                     "hf_channel", "hf_channel_known", "hf_endpoint",
                     "hf_preset_entry", "hf_preset_entry_for"],
            # v5.1 新拆的审计域（S7）。⚠️ 模块叫 auditlog 而不是 audit：
            # ``from .audit import audit`` 会把包属性 services.audit（子模块）
            # shadow 成同名函数，之后 ``from ..services import audit`` 想拿
            # 模块的代码会静默拿到函数。
            auditlog: ["AUDIT_ACTIONS", "audit", "audit_result",
                       "audit_list", "audit_count"],
        }
        checked = 0
        for mod, names in shared.items():
            for n in names:
                # ⚠️ 必须是硬断言，不能 ``if not hasattr: continue`` ——
                # 2026-10-18 真出过事：blueprints/settings.py 用了
                # ``svc._GITHUB_MIRROR_MAP``，拆 mirror 域时忘了再导出，
                # 每次保存设置都 500。旧写法对「漏导出」一律跳过，正好放跑它。
                assert hasattr(svc, n), \
                    "services 漏了再导出：%s（%s 里有）" % (
                        n, mod.__name__.rsplit(".", 1)[-1])
                checked += 1
                assert getattr(svc, n) is getattr(mod, n), \
                    ("services.%s 与 services/%s.py.%s 不是同一个对象"
                     % (n, mod.__name__.rsplit(".", 1)[-1], n))
        assert checked >= 15, "应当检查到的共享符号太少（%d），名单可能过时了" % checked

    def test_shared_container_is_live_both_ways(self):
        """双向原地改互通：域内 append 包上可见，反之亦然。"""
        import wwdm_app.services as svc
        from wwdm_app.services import clone

        assert svc.clone_queue is clone.clone_queue
        clone.clone_queue.append("__probe__")
        assert "__probe__" in svc.clone_queue
        svc.clone_queue.remove("__probe__")
        assert "__probe__" not in clone.clone_queue

    def test_commonpath_only_in_known_places(self):
        """路径校验的 commonpath 只允许出现在两处，新增即报警（防止又写第三份）。

        目前合法的两处：
          * services/output.py  —— ``_is_inside``，全项目唯一相对路径校验入口
          * services/workflows.py —— 防「备份目录位于工作流目录内部」导致
            copytree 无限递归，用途不同，不是路径穿越防护
        """
        hits = subprocess.run(
            ["git", "grep", "-n", "os.path.commonpath", "--", "wwdm_app/"],
            cwd=ROOT, stdout=subprocess.PIPE).stdout.decode("utf-8", "replace")
        allowed = ("services/output.py", "services/workflows.py")
        stray = [l for l in hits.splitlines()
                 if l.strip() and not any(a in l for a in allowed)]
        assert not stray, "别处又写了 commonpath：%s" % stray
        # 白名单里的两处必须都还在（防止有人把 _is_inside 整段删掉却没被发现）
        assert "services/output.py" in hits, "_is_inside 的 commonpath 不见了"


# ============================================================
# 模板分层
# ============================================================
class TestTemplates:
    def test_index_is_a_composition_entry(self):
        txt = open(os.path.join(PKG, "templates", "index.html"),
                   "rb").read().decode("utf-8")
        assert '{% extends "base.html" %}' in txt
        assert "<script>" not in txt            # 内联脚本已经全搬走
        assert txt.count("/static/js/pages/") == 16

    def test_base_has_all_three_blocks(self):
        txt = open(os.path.join(PKG, "templates", "base.html"),
                   "rb").read().decode("utf-8")
        for b in ("pages", "modals", "scripts"):
            assert "{%% block %s %%}" % b in txt, "base.html 少了 %s 块" % b

    def test_block_order_in_base(self):
        """顺序有讲究：pages 在 #main 里，modals 在 #main/.layout 闭合之后，
        scripts 排在外链 script 之后、右键菜单之前。"""
        txt = open(os.path.join(PKG, "templates", "base.html"),
                   "rb").read().decode("utf-8")
        i_main_open = txt.index('<div id="main"')
        i_pages = txt.index("{% block pages %}")
        i_main_close = txt.index("</div><!-- /#main -->")
        i_layout_close = txt.index("</div><!-- /.layout -->")
        i_modals = txt.index("{% block modals %}")
        i_scripts = txt.index("{% block scripts %}")
        i_ctxmenu = txt.index('id="ctxMenu"')
        assert i_main_open < i_pages < i_main_close
        assert i_main_close < i_layout_close < i_modals < i_scripts < i_ctxmenu

    def test_partials_exist(self):
        d = os.path.join(PKG, "templates", "partials")
        for name in ("launcher", "models", "plugins",
                     "workflows", "output", "input", "settings",
                     "repair", "about", "modals"):
            assert os.path.exists(os.path.join(d, name + ".html")), "缺 partial：%s" % name

    def test_no_template_tag_inside_html_comment(self):
        """HTML 注释挡不住 Jinja —— 注释里写模板标签会被当成真标签解析（踩过两次）。"""
        for root, _dirs, files in os.walk(os.path.join(PKG, "templates")):
            for f in files:
                if not f.endswith(".html"):
                    continue
                txt = open(os.path.join(root, f), "rb").read().decode("utf-8", "replace")
                for m in re.finditer(r"<!--(.*?)-->", txt, re.S):
                    body = m.group(1)
                    assert "{%" not in body and "{{" not in body, \
                        "%s 的 HTML 注释里写了模板标签" % f


# ============================================================
# 行尾约定
# ============================================================
class TestLineEndings:
    """行尾按文件钉死在 .gitattributes，漂移会让补丁脚本的锚点失效。"""

    @staticmethod
    def _eol_map():
        out = subprocess.run(["git", "ls-files", "--eol"], cwd=ROOT,
                             stdout=subprocess.PIPE).stdout.decode("utf-8", "replace")
        got = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                got[parts[1].strip()] = parts[0].strip()
        return got

    @pytest.mark.parametrize("rel", [
        "wwdm_app/core/config.py",
        "wwdm_app/routes.py",
        "wwdm_app/services/__init__.py",
        "wwdm_app/templates/index.html",
        "wwdm_app/templates/base.html",
        "wwdm_app/static/style.css",
    ])
    def test_must_be_crlf(self, rel):
        got = self._eol_map().get(rel)
        assert got and "eol=crlf" in got, "%s 应当是 CRLF，实际：%s" % (rel, got)

    @pytest.mark.parametrize("rel", [
        "wwdm_app/core/tasks.py",
        "wwdm_app/core/db.py",
        "wwdm_app/blueprints/models.py",
        "wwdm_app/services/output.py",
        "wwdm_app/static/js/taskcenter.js",
        "wwdm_app/static/js/api.js",
    ])
    def test_must_be_lf(self, rel):
        got = self._eol_map().get(rel)
        assert got and "eol=crlf" not in got, "%s 应当是 LF，实际：%s" % (rel, got)

    def test_all_templates_are_crlf(self):
        for path, attr in self._eol_map().items():
            if path.startswith("wwdm_app/templates/") and path.endswith(".html"):
                assert "eol=crlf" in attr, "%s 应当是 CRLF，实际：%s" % (path, attr)
