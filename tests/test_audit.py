# -*- coding: utf-8 -*-
"""S7 操作审计日志（services/auditlog.py + blueprints/audit.py + 各蓝线埋点）。

三条安全网在这里的体现：
  · audit_log 写在 NOTES_DB_PATH 上，conftest 已把它重定向到**临时副本** ——
    这些用例可以放心写，真 notes.db 一个字节都不会动（会话级指纹守卫兜底）；
  · 保存设置 / 危险动作在 conftest 里已打桩，不会真关机、真启动 ComfyUI；
  · 每个用例前清空临时副本里的审计表，避免互相串场。
"""
import pytest

import wwdm_app.services as svc
from wwdm_app.core import db as coredb
from wwdm_app.services import auditlog


@pytest.fixture(autouse=True)
def _clean_audit():
    """清空审计表（临时副本里的）。先确保表存在。"""
    coredb.audit_log_init()
    conn = coredb._get_notes_db()
    try:
        conn.execute("DELETE FROM audit_log")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def _snapshot_settings():
    """整字典快照还原 settings。

    本文件多个用例只带部分字段 POST /api/settings（审计只关心这一个键），
    而保存端点对**缺失的 comfyui_dir 等键会回填 DEFAULT_SETTINGS**
    （blueprints/settings.py 的 `if not d.get(...)` 系列）—— 真实路径
    C:\\tmp\\ComfyUI 于是写进活 settings，把 conftest 重定向到临时目录的
    沙箱值覆盖掉，后面 test_safety_net 的 test_managed_dirs_point_into_temp
    就会被带挂。save_settings 已打桩不会落盘，所以还原内存就够了。
    口径同 tests/test_mirror.py 的 _snapshot。
    """
    old = dict(svc.settings)
    yield
    svc.settings.clear()
    svc.settings.update(old)


# ============================================================
# 写入：铁律是「绝不反噬业务」
# ============================================================
class TestAuditWrite:
    def test_writes_row_with_ip(self, client, origin):
        """保存设置后应有一条带来源 IP 的审计（test_client 下是 127.0.0.1）"""
        r = client.post("/api/settings", headers=origin,
                        json={"hf_channel": "hf-mirror"})
        assert r.get_json()["ok"]
        rows = svc.audit_list(limit=5, action="settings.save")
        assert rows, "保存设置必须写一条审计"
        top = rows[0]
        assert top["ip"]                       # 拿到了来源 IP
        assert "hf-mirror" in top["detail"]    # 渠道信息进了详情
        assert top["ok"] == 1

    def test_writes_into_temp_copy_only(self):
        """审计必须落在 conftest 重定向的临时副本里，不能碰真 notes.db"""
        assert "wwdm_pytest_" in coredb.NOTES_DB_PATH, \
            "沙箱没生效就跑审计测试，会脏真库：%s" % coredb.NOTES_DB_PATH

    def test_no_request_context_does_not_explode(self):
        """后台线程 / CLI 场景没有请求上下文，取不到 IP 也不能炸"""
        svc.audit("settings.save", target="后台线程")
        rows = svc.audit_list(limit=5, action="settings.save")
        assert any(r["target"] == "后台线程" for r in rows)
        assert rows[0]["ip"] == ""

    def test_db_failure_swallowed(self, monkeypatch):
        """写库炸了不能反噬业务 —— 与任务总线同一条铁律"""
        def boom(*a, **k):
            raise RuntimeError("disk full")
        monkeypatch.setattr(coredb, "audit_log_add", boom)
        svc.audit("model.delete", target="x")      # 不应抛异常

    def test_failure_flag_recorded(self):
        """失败的操作也要记（ok=0）—— 「谁动过、动成功没有」都有价值"""
        svc.audit("comfyui.stop", target="ComfyUI", detail="未在运行", ok=False)
        rows = svc.audit_list(limit=5, action="comfyui.stop")
        assert rows and rows[0]["ok"] == 0

    def test_reexport_is_same_object(self):
        assert svc.audit is auditlog.audit
        assert svc.AUDIT_ACTIONS is auditlog.AUDIT_ACTIONS

    def test_package_attribute_is_function_not_module(self):
        """services.audit 必须是**函数**。

        2026-10-18 的坑：模块原本叫 audit.py，``from .audit import audit``
        会把包属性 services.audit（子模块）shadow 成同名函数，之后
        ``from ..services import audit`` 想拿模块的代码会静默拿到函数。
        改名 auditlog.py 规避，这条断言守住决定。
        """
        assert callable(svc.audit)
        assert not hasattr(svc.audit, "audit_list")


# ============================================================
# 存储层：查询 / 过滤 / 裁剪
# ============================================================
class TestAuditStorage:
    def test_list_order_and_shape(self):
        for i in range(3):
            svc.audit("model.delete", target="m%d" % i)
        rows = svc.audit_list(limit=10, action="model.delete")
        assert [r["target"] for r in rows] == ["m2", "m1", "m0"]   # 新的在前
        assert {"id", "ts", "action", "target", "detail", "ip", "ok"} <= set(rows[0])

    def test_filter_by_action(self):
        svc.audit("model.delete", target="a")
        svc.audit("shutdown.schedule", target="b")
        rows = svc.audit_list(limit=10, action="shutdown.schedule")
        assert len(rows) == 1 and rows[0]["target"] == "b"

    def test_filter_by_keyword(self):
        svc.audit("model.delete", target="foo.safetensors")
        svc.audit("model.delete", target="bar.safetensors")
        rows = svc.audit_list(limit=10, kw="foo")
        assert len(rows) == 1 and "foo" in rows[0]["target"]

    def test_bad_limit_degrades(self):
        svc.audit("model.delete", target="a")
        assert coredb.audit_log_list(limit="abc") != []
        assert len(coredb.audit_log_list(limit=0)) == 1     # 0 → 至少 1 条
        assert len(coredb.audit_log_list(limit=-5)) == 1

    def test_prune_keeps_recent(self, monkeypatch):
        """只留最近 N 条 —— 打小 AUDIT_KEEP 免得插两千行"""
        monkeypatch.setattr(coredb, "AUDIT_KEEP", 5)
        for i in range(10):
            coredb.audit_log_add("model.delete", target="m%d" % i)
        assert coredb.audit_log_count() == 5
        targets = [r["target"] for r in coredb.audit_log_list(limit=10)]
        assert targets == ["m9", "m8", "m7", "m6", "m5"]     # 留的是最新的

    def test_long_values_truncated(self):
        coredb.audit_log_add("model.delete", target="t" * 900, detail="d" * 9000)
        row = coredb.audit_log_list(limit=1)[0]
        assert len(row["target"]) <= 500 and len(row["detail"]) <= 2000


# ============================================================
# 埋点归一化：audit_result 的返回形态适配
# ============================================================
class TestAuditResultNormalize:
    """服务层返回 dict，conftest 桩返回 (ok, msg) 元组。

    埋点处直接 ``r.get(...)`` 会在**求参阶段** AttributeError，
    audit() 自己的 try/except 兜不住参数求值 → 业务接口 500。
    2026-10-18 真事：``/api/shutdown`` 与 ``/api/comfyui/start`` 全中招。
    """

    def test_dict_form(self):
        svc.audit_result("comfyui.start", {"ok": True, "msg": "ComfyUI 已启动"},
                         target="D:/ComfyUI")
        row = svc.audit_list(limit=5, action="comfyui.start")[0]
        assert row["ok"] == 1 and row["target"] == "D:/ComfyUI"
        assert "ComfyUI 已启动" in row["detail"]

    def test_tuple_form(self):
        """conftest 桩形：("stubbed", 函数名)"""
        svc.audit_result("shutdown.schedule", ("stubbed", "schedule_shutdown"),
                         target="60 秒")
        row = svc.audit_list(limit=5, action="shutdown.schedule")[0]
        assert row["ok"] == 1                      # bool("stubbed") 为真
        assert "schedule_shutdown" in row["detail"]

    def test_failure_flag_from_tuple(self):
        svc.audit_result("comfyui.stop", (False, "ComfyUI 未在运行"))
        row = svc.audit_list(limit=5, action="comfyui.stop")[0]
        assert row["ok"] == 0 and "未在运行" in row["detail"]

    def test_extra_prefix(self):
        svc.audit_result("localfile.delete",
                         {"ok": False, "msg": "拒绝访问"}, extra="永久删除")
        row = svc.audit_list(limit=5, action="localfile.delete")[0]
        assert row["detail"] == "永久删除 · 拒绝访问"
        assert row["ok"] == 0

    def test_extra_without_msg(self):
        svc.audit_result("pip.install", {"ok": True}, target="PyTorch",
                         extra="pip install torch")
        row = svc.audit_list(limit=5, action="pip.install")[0]
        assert row["detail"] == "pip install torch"   # 不为空也不带 " · "

    def test_other_form(self):
        svc.audit_result("pip.install", None, target="torch")
        row = svc.audit_list(limit=5, action="pip.install")[0]
        assert row["detail"] == "" and row["ok"] == 1

    def test_reexport_is_same_object(self):
        assert svc.audit_result is auditlog.audit_result

    def test_endpoints_with_stubbed_dangerous(self, client, origin):
        """整链路：打桩的关机/启停走 HTTP 不许 500，且都留下审计"""
        assert client.post("/api/shutdown", headers=origin,
                           json={"delay": 30}).status_code == 200
        assert svc.audit_list(limit=5, action="shutdown.schedule")
        assert client.post("/api/shutdown/cancel",
                           headers=origin).status_code == 200
        assert svc.audit_list(limit=5, action="shutdown.cancel")
        assert client.post("/api/comfyui/start",
                           headers=origin).status_code == 200
        assert svc.audit_list(limit=5, action="comfyui.start")
        assert client.post("/api/comfyui/stop",
                           headers=origin).status_code == 200
        assert svc.audit_list(limit=5, action="comfyui.stop")


# ============================================================
# 读取 API
# ============================================================
class TestAuditApi:
    def test_get_returns_actions_and_logs(self, client, origin):
        client.post("/api/settings", headers=origin, json={"hf_channel": "official"})
        j = client.get("/api/audit?limit=10").get_json()
        assert j["ok"]
        assert j["actions"]["settings.save"] == "保存设置"
        assert "settings.save" in [r["action"] for r in j["logs"]]

    def test_filter_by_action(self, client, origin):
        client.post("/api/settings", headers=origin, json={"hf_channel": "hf-mirror"})
        j = client.get("/api/audit?action=settings.save&limit=5").get_json()
        assert j["logs"] and all(r["action"] == "settings.save" for r in j["logs"])

    def test_unknown_action_returns_empty(self, client):
        j = client.get("/api/audit?action=__nope__").get_json()
        assert j["logs"] == []

    def test_keyword_filter(self, client, origin):
        client.post("/api/settings", headers=origin, json={"hf_channel": "hf-mirror"})
        j = client.get("/api/audit?kw=hf-mirror").get_json()
        assert any("hf-mirror" in (r["detail"] or "") for r in j["logs"])

    def test_bad_limit_degrades(self, client):
        assert client.get("/api/audit?limit=abc").status_code == 200

    def test_limit_clamped(self, client):
        """limit=99999 不许把整表拉走（存储层 clamp 到 500）"""
        assert len(client.get("/api/audit?limit=99999").get_json()["logs"]) <= 500


# ============================================================
# 埋点覆盖：每条危险写操作都要能在审计里找到
# ============================================================
class TestInstrumentation:
    def test_settings_reset_and_token(self, client, origin):
        client.post("/api/settings/regenerate_token", headers=origin)
        rows = svc.audit_list(limit=5, action="token.regenerate")
        assert rows, "重新生成令牌必须记审计"

    def test_shutdown_schedule(self, client, origin):
        """schedule_shutdown 在 conftest 里已打桩，不会真关机"""
        r = client.post("/api/shutdown", headers=origin, json={"delay": 60})
        assert r.status_code == 200
        rows = svc.audit_list(limit=5, action="shutdown.schedule")
        assert rows, "定时关机必须记审计"

    def test_clone_start(self, client, origin):
        """start_clone 已打桩；返回 (ok,msg) 元组，埋点要能正确取到"""
        r = client.post("/api/plugins/clone/999999", headers=origin)
        assert r.status_code == 200
        rows = svc.audit_list(limit=5, action="clone.start")
        assert rows, "发起克隆必须记审计"

    def test_actions_cover_all_instrumented_keys(self):
        """埋点用到的每个 key 都必须在 AUDIT_ACTIONS 里（否则 UI 显示原始 key）"""
        used = {"model.delete", "localfile.delete", "localfile.rename",
                "localfile.move", "localfile.mkdir", "localfile.upload",
                "plugin.delete", "comfyui.start", "comfyui.stop",
                "clone.start", "repair.run", "pip.install",
                "shutdown.schedule", "shutdown.cancel", "settings.save",
                "settings.reset", "token.regenerate", "system.quit"}
        assert used <= set(auditlog.AUDIT_ACTIONS)
