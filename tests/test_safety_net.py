# -*- coding: utf-8 -*-
"""安全网自检 —— 测「测试基础设施」本身。

这些断言不测业务，测的是 conftest 搭的三条安全网还活着。
没有它们，一旦有人改坏 conftest，上面那一百多条测试会全在**真库**上裸奔，
而表面看起来还是绿油油的 —— 那才是最危险的失败模式。

背景（2026-10-17 事故）：一个校验脚本把真库 models.db 插了一条 probe 记录，
指纹从 febbf9354161 永久变成 37334228dc2e，再也回不去。
"""
import hashlib
import os

import pytest

from conftest import FINGERPRINT_BEFORE, TMP_ROOT, WATCHED, _sha


class TestFingerprintGuard:
    def test_baseline_was_recorded_before_import(self):
        """三个基线必须都取到了 —— 取不到说明有人把那段挪到 import 后面了。"""
        assert len(FINGERPRINT_BEFORE) == 3
        for k, v in FINGERPRINT_BEFORE.items():
            assert v, "%s 的指纹基线是空的" % k

    def test_baseline_matches_disk_right_now(self):
        """跑到这时真库仍应与基线一致（会话结束还有一次总检查）。"""
        for k, path in WATCHED.items():
            assert _sha(path) == FINGERPRINT_BEFORE[k], "%s 已被改动" % k

    def test_guard_is_session_scoped_autouse(self):
        """守卫必须是 autouse，否则「忘了引用」就等于没有。"""
        import conftest
        src = open(conftest.__file__, "rb").read().decode("utf-8")
        assert 'scope="session"' in src and "autouse=True" in src
        assert "FINGERPRINT_BEFORE" in src


class TestDatabaseSandbox:
    def test_db_paths_point_into_temp(self):
        """core.db 用的必须是副本，不是 wwdm_app/static/info 下的真库。"""
        from wwdm_app.core import db as coredb
        for attr in ("DB_PATH", "NOTES_DB_PATH"):
            p = getattr(coredb, attr)
            assert os.path.isabs(p), "%s 不是绝对路径" % attr
            assert TMP_ROOT in p, "%s 没指到临时目录：%s" % (attr, p)
            assert os.path.exists(p), "%s 指向的文件不存在" % attr

    def test_db_paths_differ_from_config_defaults(self):
        """副本路径不能等于 config 里的默认值（那种情况说明替换没生效）。"""
        from wwdm_app.core import config
        from wwdm_app.core import db as coredb
        assert coredb.DB_PATH != config.DB_PATH
        assert coredb.NOTES_DB_PATH != config.NOTES_DB_PATH

    def test_real_db_files_untouched_on_disk(self):
        """真库文件本身一个字节都没变。"""
        for k in ("models.db", "notes.db"):
            assert os.path.exists(WATCHED[k])
            assert _sha(WATCHED[k]) == FINGERPRINT_BEFORE[k]


class TestSettingsSandbox:
    def test_save_is_stubbed(self):
        """save_settings 必须是打桩的，不能真的落盘。"""
        from wwdm_app.core import config
        before = _sha(WATCHED["settings.json"])
        config.save_settings({"anything": 1})
        config.save_settings()
        assert _sha(WATCHED["settings.json"]) == before

    def test_managed_dirs_point_into_temp(self):
        """output / input / workflow / comfyui 目录都被重定向到临时目录。"""
        from wwdm_app.core import config
        for k in ("output_dir", "input_dir", "workflow_dir", "comfyui_dir"):
            if k in config.settings:
                assert TMP_ROOT in str(config.settings[k]), \
                    "%s 没被重定向：%s" % (k, config.settings[k])


class TestDangerousActionsStubbed:
    @pytest.mark.parametrize("name", [
        "schedule_shutdown", "start_clone", "_git_clone_thread",
        "aria2_start", "aria2_stop", "start_comfyui", "stop_comfyui",
    ])
    def test_stubbed(self, name):
        """危险动作被调用时必须只是记一笔，真动作绝不能发生。"""
        import wwdm_app.services as svc
        if not hasattr(svc, name):
            pytest.skip("没有这个符号：%s" % name)
        got = getattr(svc, name)("probe-arg")
        assert got == ("stubbed", name)

    def test_stubs_are_restored_after_session(self):
        """打桩是 session 级的，结束后要还原 —— 这里只验证还原逻辑写在了 conftest 里。"""
        import conftest
        src = open(conftest.__file__, "rb").read().decode("utf-8")
        assert "setattr(svc, n, fn)" in src, "打桩没写还原逻辑"


class TestTasksIsolated:
    def test_bus_is_empty_at_start(self):
        """autouse 的 _tasks_clean 保证用例之间不串场。"""
        from wwdm_app.core import tasks
        assert tasks.snapshot()["total"] == 0

    def test_leftovers_do_not_leak(self):
        from wwdm_app.core import tasks
        tasks.upsert("leak:1", "leak", status=tasks.DONE)
        assert tasks.snapshot()["total"] == 1     # 本用例内可见
        # 下一个用例开头应当是 0（由 _tasks_clean 保证）
