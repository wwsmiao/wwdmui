# -*- coding: utf-8 -*-
"""任务总线（core/tasks.py）—— 纯内存逻辑，不依赖 Flask，可以放心快测。

重点覆盖三件事：
  1. 上报语义：局部更新、终态、重跑清 finished_at、进度 -1 的特殊含义
  2. 快照语义：在跑的优先、终态倒序、按 kind 过滤、limit 不挤掉在跑的
  3. 清理语义：只清终态、skipped 的口径是「点名要删却因为在跑被拒」
"""
import threading
import time

import pytest

from wwdm_app.core import tasks as T


# ============================================================
# id 与基本写入
# ============================================================
class TestIds:
    @pytest.mark.parametrize("kind,key,want", [
        ("download", 12, "download:12"),
        ("branches", r"D:\ComfyUI", "branches:D:\\ComfyUI"),
        ("repair", "", "repair"),
    ])
    def test_make_id(self, kind, key, want):
        assert T.make_id(kind, key) == want

    def test_same_thing_reports_back_to_same_row(self):
        """同一件事反复上报必须落回同一条，不能每次新建。"""
        T.upsert("download:7", "download", title="a")
        T.upsert("download:7", "download", title="b")
        snap = T.snapshot()
        assert snap["total"] == 1
        assert snap["tasks"][0]["title"] == "b"

    def test_kind_inferred_from_id_when_missing(self):
        """收尾时没先 upsert 也要落出正确的 kind，否则 ?kind=clone 永远筛不到。"""
        T.fail("clone:orphan", "boom")
        got = T.snapshot(kinds=["clone"])
        assert got["total"] == 1
        assert got["tasks"][0]["kind"] == "clone"


class TestUpsert:
    def test_partial_update_does_not_wipe_fields(self):
        """只覆盖显式传入（非 None）的字段。"""
        T.upsert("x:1", "x", title="标题", message="消息", status=T.RUNNING, progress=10.0)
        T.upsert("x:1", None, progress=50.0)
        t = T.get("x:1")
        assert t["title"] == "标题"          # 没传，保留
        assert t["message"] == "消息"
        assert t["status"] == T.RUNNING
        assert t["progress"] == 50.0          # 传了，覆盖

    def test_bad_progress_is_ignored_not_crash(self):
        T.upsert("x:1", "x", progress="not-a-number")
        assert T.get("x:1")["progress"] == T.PROGRESS_UNKNOWN

    def test_detail_is_replaced_whole(self):
        T.upsert("x:1", "x", detail={"a": 1})
        T.upsert("x:1", None, detail={"b": 2})
        assert T.get("x:1")["detail"] == {"b": 2}

    def test_snapshot_returns_deep_copy(self):
        """调用方随便改快照，不能串场到注册表。"""
        T.upsert("x:1", "x", detail={"a": 1})
        s = T.snapshot()["tasks"][0]
        s["detail"]["a"] = 999
        s["title"] = "被改坏了"
        assert T.get("x:1")["detail"]["a"] == 1

    def test_seq_is_monotonic(self):
        a = T.upsert("x:1", "x")["seq"]
        b = T.upsert("x:2", "x")["seq"]
        c = T.upsert("x:1", "x", message="x")["seq"]
        assert a < b < c


# ============================================================
# 终态与重跑
# ============================================================
class TestFinish:
    @pytest.mark.parametrize("fn,status", [
        (lambda i: T.finish(i, message="ok"), T.DONE),
        (lambda i: T.fail(i, message="boom"), T.FAILED),
        (lambda i: T.cancel(i), T.CANCELED),
    ])
    def test_status_and_finished_at(self, fn, status):
        T.upsert("x:1", "x", status=T.RUNNING)
        assert T.get("x:1")["finished_at"] == 0.0
        fn("x:1")
        t = T.get("x:1")
        assert t["status"] == status
        assert t["finished_at"] > 0

    def test_finish_sets_progress_100_only_for_done(self):
        T.finish("x:1", status=T.DONE)
        assert T.get("x:1")["progress"] == 100.0
        T.fail("x:2")
        assert T.get("x:2")["progress"] == T.PROGRESS_UNKNOWN  # 失败不该显示 100%

    def test_rerun_clears_finished_at(self):
        """从终态跨回非终态 = 重跑，finished_at 必须清掉。"""
        T.finish("x:1", status=T.DONE)
        assert T.get("x:1")["finished_at"] > 0
        T.upsert("x:1", None, status=T.RUNNING)
        assert T.get("x:1")["finished_at"] == 0.0

    def test_finish_with_non_final_status_falls_back_to_done(self):
        T.finish("x:1", status="running")     # 传了个非终态
        assert T.get("x:1")["status"] == T.DONE


class TestProgressUnknown:
    def test_minus_one_means_unknown_not_zero(self):
        """-1 = 「在跑但算不出百分比」，前端必须渲染成流动条纹。"""
        assert T.PROGRESS_UNKNOWN == -1.0
        T.upsert("x:1", "x", status=T.RUNNING)   # 不传 progress
        assert T.get("x:1")["progress"] == T.PROGRESS_UNKNOWN


# ============================================================
# 快照
# ============================================================
class TestSnapshot:
    def test_actives_first_then_finished_newest_first(self, seed_tasks):
        seed_tasks()
        tasks = T.snapshot()["tasks"]
        # 前两条在跑（按 started_at 升序），后四条终态（按 updated_at 倒序）
        assert [t["status"] for t in tasks[:2]] == [T.RUNNING, T.PENDING]
        assert all(t["status"] in T.FINISHED for t in tasks[2:])
        ts = [t["updated_at"] for t in tasks[2:]]
        assert ts == sorted(ts, reverse=True)

    def test_limit_never_squeezes_out_running(self, seed_tasks):
        """limit=1 时给的是「在跑的」，不是「最新完成的那条」。"""
        seed_tasks()
        got = T.snapshot(limit=1)["tasks"]
        assert len(got) == 1
        assert got[0]["status"] not in T.FINISHED

    def test_filter_by_kind(self, seed_tasks):
        seed_tasks()
        assert T.snapshot(kinds=["clone"])["total"] == 1
        assert T.snapshot(kinds=["download"])["total"] == 3
        assert T.snapshot(kinds=["nonexistent"])["total"] == 0

    def test_active_only(self, seed_tasks):
        seed_tasks()
        got = T.snapshot(active_only=True)
        assert got["total"] == 2
        assert got["active"] == 2

    def test_counts(self, seed_tasks):
        seed_tasks()
        s = T.snapshot()
        assert s["total"] == 6 and s["active"] == 2
        assert T.active_count() == 2


# ============================================================
# 清理
# ============================================================
class TestClear:
    def test_scope_failed_only(self, seed_tasks):
        seed_tasks()
        r = T.clear(scope="failed")
        assert r == {"removed": 2, "skipped": 0}
        left = {t["id"] for t in T.snapshot()["tasks"]}
        assert left == {"download:run", "download:pend", "repair:d1", "repair:cc"}

    def test_scope_finished_keeps_running(self, seed_tasks):
        seed_tasks()
        r = T.clear(scope="finished")
        assert r["removed"] == 4
        assert T.snapshot()["total"] == 2

    def test_scope_done_only(self, seed_tasks):
        seed_tasks()
        assert T.clear(scope="done")["removed"] == 1

    def test_unknown_scope_degrades_to_finished(self, seed_tasks):
        """不认识的 scope 一律按「全部终态」—— 宁可少删，不许多删。"""
        seed_tasks()
        assert T.clear(scope="NOT_A_REAL_SCOPE")["removed"] == 4
        assert "download:run" in {t["id"] for t in T.snapshot()["tasks"]}

    def test_by_id_skips_running(self, seed_tasks):
        seed_tasks()
        r = T.clear(ids=["download:run"])
        assert r == {"removed": 0, "skipped": 1}

    def test_by_id_removes_finished(self, seed_tasks):
        seed_tasks()
        r = T.clear(ids=["download:f1", "repair:d1"])
        assert r == {"removed": 2, "skipped": 0}

    def test_skipped_counts_only_named_ids(self, seed_tasks):
        """口径：点名要删、却因为在跑被拒。按 scope 清时恒为 0（不是「遍历到没删」）。"""
        seed_tasks()
        assert T.clear(scope="failed")["skipped"] == 0      # 虽然后面还有 4 条没删
        assert T.clear(scope="finished")["skipped"] == 0
        assert T.clear(ids=["download:run"])["skipped"] == 1

    def test_nothing_to_clear(self):
        assert T.clear(scope="failed") == {"removed": 0, "skipped": 0}

    def test_remove_single(self, seed_tasks):
        seed_tasks()
        assert T.remove("download:f1") is True
        assert T.remove("download:f1") is False    # 第二次已经没了

    def test_reset(self, seed_tasks):
        seed_tasks()
        T.reset()
        assert T.snapshot()["total"] == 0


# ============================================================
# 内存有界：MAX_DONE / DONE_TTL
# ============================================================
class TestPrune:
    def test_max_done(self, monkeypatch):
        monkeypatch.setattr(T, "MAX_DONE", 3)
        for i in range(10):
            T.finish("x:%d" % i, status=T.DONE)
            time.sleep(0.001)          # 保证 updated_at 有先后
        assert T.snapshot()["total"] == 3

    def test_ttl(self, monkeypatch):
        monkeypatch.setattr(T, "DONE_TTL", 0.05)
        T.finish("x:old", status=T.DONE)
        time.sleep(0.12)
        T.upsert("x:new", "x", status=T.DONE)   # 触发一次剪裁
        assert T.get("x:old") is None
        assert T.get("x:new") is not None

    def test_running_are_never_pruned(self, monkeypatch):
        monkeypatch.setattr(T, "MAX_DONE", 1)
        for i in range(5):
            T.upsert("r:%d" % i, "x", status=T.RUNNING)
        assert T.snapshot()["total"] == 5     # 在跑的不受 MAX_DONE 限制


# ============================================================
# 并发
# ============================================================
class TestConcurrency:
    def test_parallel_upsert_does_not_corrupt(self):
        """后台线程会并发上报，这里压一下确保锁是真管用的。"""
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    T.upsert("w:%d" % (n % 8), "x", progress=float(i))
            except Exception as e:      # pragma: no cover
                errors.append(e)

        ths = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

        assert not errors
        assert T.snapshot()["total"] == 8

    def test_snapshot_during_writes(self):
        """读的同时有人在写，快照也得是完整可用的（不能拿到半截状态）。"""
        stop = threading.Event()

        def writer():
            i = 0
            while not stop.is_set():
                T.upsert("c:%d" % (i % 5), "x", progress=float(i))
                i += 1

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            for _ in range(50):
                s = T.snapshot()
                assert "tasks" in s and "seq" in s
        finally:
            stop.set()
            t.join(timeout=2)
