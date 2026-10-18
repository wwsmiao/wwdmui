# -*- coding: utf-8 -*-
"""ComfyUI 日志环形缓冲的 since/reset 语义。

背景：v6.4 之前日志无上限、且逐行读 stdio；长会话后一次 since=0 的拉取会
切片巨表 + JSON 化，Flask 线程长时间持锁 → 读管道线程被堵 → ComfyUI 的
print 写满管道 → 主线程卡住（点「运行」几十秒才生效）。这里守住新语义。
"""
import wwdm_app.services as svc


class TestComfyLogRing:
    def setup_method(self):
        svc.comfyui_logs = []
        svc.comfyui_log_base = 0

    def test_empty_append_ignored(self):
        svc._comfy_log_append("")
        assert svc.comfyui_logs == []

    def test_ring_trim_and_reset(self):
        cap = svc._COMFY_LOG_CAP
        for i in range(cap + 50):
            svc._comfy_log_append("line-%d" % i)
        assert len(svc.comfyui_logs) == cap
        assert svc.comfyui_log_base == 50
        total = 50 + cap

        # 客户端索引早于缓冲起点 → reset，返回当前全部
        r = svc.get_comfyui_logs(0)
        assert r.get("reset") is True
        assert r["next_index"] == total
        assert len(r["lines"]) == cap

        # 客户端在缓冲区间内 → 只给增量
        r2 = svc.get_comfyui_logs(total - 3)
        assert "reset" not in r2
        assert r2["lines"] == ["line-%d" % (total - 3 + k) for k in range(3)]
        assert r2["next_index"] == total

        # 已经追平 → 空增量
        r3 = svc.get_comfyui_logs(total)
        assert r3["lines"] == []
        assert r3["next_index"] == total
