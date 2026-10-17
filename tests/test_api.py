# -*- coding: utf-8 -*-
"""HTTP 层回归：只读端点、任务清理端点、安全闸、统一错误体。

全部走 Flask test_client，不起真服务 —— 快，且不占 7860/7861。
⚠️ 同源校验要求 Origin 与 host 一致；test_client 下就是 ``http://localhost``。
"""
import pytest

from wwdm_app.core import tasks as T

PAGES = ["launcher", "models", "plugins", "douyin", "workflows",
         "help", "output", "input", "settings", "repair", "about"]
# v5.1 起「笔记本」不再是页面：partials/notes.html 改为 FAB 唤出的 .lgmodal
# 大弹窗（#notesModal），断言从 PAGES 挪到下面的骨架锚点里。


# ============================================================
# 首页
# ============================================================
class TestIndex:
    def test_ok(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert len(r.data) > 100000

    @pytest.mark.parametrize("page", PAGES)
    def test_page_containers(self, client, page):
        assert ('id="page-%s"' % page) in client.get("/").get_data(as_text=True)

    @pytest.mark.parametrize("anchor", [
        'id="sidebar"', 'id="bannerWrap"', 'id="fabWrap"', 'id="main"',
        'id="tcDraw"', 'id="tcMask"', 'id="tcList"', 'id="ctxMenu"',
        'id="tcActions"',              # 任务中心清理区
        'id="mbg"', 'id="batmbg"', 'id="pmbg"', 'id="rlogModal"',
        'id="notesModal"',             # v5.1：笔记本弹窗（原 page-notes 页面）
    ])
    def test_skeleton_anchors(self, client, anchor):
        assert anchor in client.get("/").get_data(as_text=True)

    def test_no_unrendered_jinja(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "{%" not in html and "{{" not in html

    def test_scripts_order(self, client):
        """公共件必须排在页面脚本之前 —— 页面脚本顶层直接用了它们的全局符号。"""
        html = client.get("/").get_data(as_text=True)
        last_common = max(html.index(s) for s in (
            "/static/js/ui.js", "/static/js/api.js", "/static/js/poll.js",
            "/static/js/table.js", "/static/js/taskcenter.js"))
        first_page = min(html.index("/static/js/pages/%s.js" % n) for n in (
            "banner", "common", "output", "ctxmenu", "preview", "models",
            "localfiles", "plugins", "clonelog", "launcher", "input", "douyin",
            "repair", "init", "notes", "workflows", "wfanalyze"))
        assert last_common < first_page

    def test_all_page_scripts_referenced(self, client):
        html = client.get("/").get_data(as_text=True)
        assert html.count("/static/js/pages/") == 17

    @pytest.mark.parametrize("path", [
        "/static/js/ui.js", "/static/js/api.js", "/static/js/poll.js",
        "/static/js/table.js", "/static/js/taskcenter.js", "/static/style.css",
        "/static/js/pages/banner.js", "/static/js/pages/localfiles.js",
    ])
    def test_static_assets(self, client, path):
        assert client.get(path).status_code == 200


# ============================================================
# 只读端点
# ============================================================
class TestReadonlyApi:
    @pytest.mark.parametrize("path", [
        "/api/tasks",
        "/api/models?page=1&page_size=5",
        "/api/plugins?page=1&page_size=5",
        "/api/settings",
        "/api/aria2/status",
        "/api/comfyui/status",
    ])
    def test_get_200(self, client, path):
        r = client.get(path)
        assert r.status_code == 200, "%s -> %s" % (path, r.status_code)

    def test_tasks_shape(self, client, seed_tasks):
        seed_tasks()
        j = client.get("/api/tasks").get_json()
        assert j["total"] == 6 and j["active"] == 2
        assert len(j["tasks"]) == 6
        assert {"id", "kind", "status", "progress", "title"} <= set(j["tasks"][0])

    def test_tasks_kind_filter(self, client, seed_tasks):
        seed_tasks()
        assert client.get("/api/tasks?kind=clone").get_json()["total"] == 1

    def test_tasks_active_only(self, client, seed_tasks):
        seed_tasks()
        assert client.get("/api/tasks?active=1").get_json()["total"] == 2

    def test_tasks_limit(self, client, seed_tasks):
        seed_tasks()
        assert len(client.get("/api/tasks?limit=2").get_json()["tasks"]) == 2

    def test_bad_limit_does_not_500(self, client, seed_tasks):
        seed_tasks()
        assert client.get("/api/tasks?limit=abc").status_code == 200


# ============================================================
# 任务清理端点（DELETE /api/tasks）
# ============================================================
class TestClearApi:
    def test_scope_failed(self, client, seed_tasks, origin):
        seed_tasks()
        r = client.delete("/api/tasks?scope=failed", headers=origin)
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] is True
        assert j["removed"] == 2 and j["skipped"] == 0 and j["running"] == 2

    def test_scope_finished(self, client, seed_tasks, origin):
        seed_tasks()
        j = client.delete("/api/tasks?scope=finished", headers=origin).get_json()
        assert j["removed"] == 4
        assert client.get("/api/tasks").get_json()["total"] == 2

    def test_by_id(self, client, seed_tasks, origin):
        seed_tasks()
        j = client.delete("/api/tasks?id=download:f1,clone:f2", headers=origin).get_json()
        assert j["removed"] == 2 and j["skipped"] == 0

    def test_running_cannot_be_deleted(self, client, seed_tasks, origin):
        seed_tasks()
        j = client.delete("/api/tasks?id=download:run", headers=origin).get_json()
        assert j["removed"] == 0 and j["skipped"] == 1
        assert "download:run" in {t["id"] for t in
                                  client.get("/api/tasks").get_json()["tasks"]}

    def test_json_body_scope(self, client, seed_tasks, origin):
        seed_tasks()
        r = client.delete("/api/tasks", headers=origin, json={"scope": "failed"})
        assert r.get_json()["removed"] == 2

    def test_json_body_ids(self, client, seed_tasks, origin):
        seed_tasks()
        r = client.delete("/api/tasks", headers=origin,
                          json={"ids": ["clone:f2", "repair:d1"]})
        assert r.get_json()["removed"] == 2

    def test_unknown_scope_degrades(self, client, seed_tasks, origin):
        seed_tasks()
        j = client.delete("/api/tasks?scope=NOPE", headers=origin).get_json()
        assert j["removed"] == 4          # 退化为 finished，宁可少删
        assert j["running"] == 2          # 在跑的没被误伤

    @pytest.mark.parametrize("q", ["id=not-exist", "id=", "scope=", ""])
    def test_edge_inputs_do_not_500(self, client, seed_tasks, origin, q):
        seed_tasks()
        r = client.delete("/api/tasks?" + q, headers=origin)
        assert r.status_code == 200

    def test_get_never_deletes(self, client, seed_tasks):
        """GET 就算带上 scope 参数也必须是只读的。"""
        seed_tasks()
        before = client.get("/api/tasks").get_json()["total"]
        client.get("/api/tasks?scope=finished")
        assert client.get("/api/tasks").get_json()["total"] == before

    def test_idempotent(self, client, seed_tasks, origin):
        seed_tasks()
        client.delete("/api/tasks?scope=finished", headers=origin)
        j = client.delete("/api/tasks?scope=finished", headers=origin).get_json()
        assert j["removed"] == 0


# ============================================================
# 安全闸
# ============================================================
class TestSecurity:
    def test_cross_site_write_rejected(self, client, seed_tasks):
        seed_tasks()
        r = client.delete("/api/tasks?scope=finished",
                          headers={"Origin": "http://evil.example.com"})
        assert r.status_code == 403
        assert client.get("/api/tasks").get_json()["total"] == 6   # 数据没被动

    def test_same_origin_write_allowed(self, client, seed_tasks, origin):
        seed_tasks()
        assert client.delete("/api/tasks?scope=failed", headers=origin).status_code == 200

    def test_no_origin_allowed(self, client, seed_tasks):
        """curl / 脚本不带 Origin，同源校验应当放行（「不存在则放行」）。"""
        seed_tasks()
        assert client.delete("/api/tasks?scope=failed").status_code == 200

    def test_loopback_needs_no_token(self, client):
        """回环地址一律放行，本机用户不该被自己拦下。"""
        assert client.get("/api/settings").status_code == 200

    def test_rejected_write_body_has_trace_id(self, client, seed_tasks):
        seed_tasks()
        r = client.delete("/api/tasks?scope=finished",
                          headers={"Origin": "http://evil.example.com"})
        j = r.get_json()
        assert j["ok"] is False
        assert j.get("trace_id")


# ============================================================
# 统一错误体
# ============================================================
class TestErrorPayload:
    def test_404_shape(self, client):
        r = client.get("/api/definitely-not-exist")
        j = r.get_json()
        assert r.status_code == 404
        assert j["ok"] is False
        assert j.get("msg")
        assert j.get("trace_id")

    def test_trace_id_echoed(self, client):
        r = client.get("/api/definitely-not-exist", headers={"X-Trace-Id": "abc123"})
        assert r.get_json()["trace_id"] == "abc123"

    def test_trace_id_in_response_header(self, client):
        r = client.get("/api/definitely-not-exist")
        assert r.headers.get("X-Trace-Id")
