"""镜像域（services/mirror.py）单测。

覆盖 v5.1 新增/改造的两块：
  * HuggingFace 渠道切换 —— 原来写死 hf-mirror，用户无法选官方
  * GitHub 镜像表扩展 + 「测速自动选最快」

所有涉及 settings 的用例都走原地改 + 还原，绝不把 hf_channel 落盘。
"""
import pytest

import wwdm_app.services as svc
from wwdm_app.services import mirror


@pytest.fixture
def hf(monkeypatch):
    """临时改 HF 渠道，用完还原"""
    def _set(key):
        old = svc.settings.get("hf_channel", "hf-mirror")
        svc.settings["hf_channel"] = key
        return old
    old = _set("hf-mirror")
    yield _set
    svc.settings["hf_channel"] = old


# ============================================================
# HuggingFace 渠道
# ============================================================
class TestHfChannel:
    def test_default_is_hf_mirror(self):
        """默认保持 hf-mirror —— 不改变既有用户体验"""
        assert mirror.hf_channel() in ("hf-mirror", "official")
        assert mirror._HF_CHANNEL_MAP[mirror.hf_channel()]

    def test_unknown_key_falls_back(self):
        svc.settings["hf_channel"] = "__bogus__"
        try:
            assert mirror.hf_channel() == "hf-mirror"
            assert mirror.hf_endpoint() == "https://hf-mirror.com"
        finally:
            svc.settings.pop("hf_channel", None)

    def test_hf_mirror_rewrites_url(self, hf):
        hf("hf-mirror")
        u = "https://huggingface.co/foo/bar/resolve/main/x.safetensors"
        assert mirror.convert_to_mirror(u) == \
            "https://hf-mirror.com/foo/bar/resolve/main/x.safetensors"

    def test_official_keeps_url(self, hf):
        """官方渠道必须原样返回 —— 这是本次改造的核心目的"""
        hf("official")
        u = "https://huggingface.co/foo/bar/resolve/main/x.safetensors"
        assert mirror.convert_to_mirror(u) == u

    def test_non_hf_url_untouched(self, hf):
        hf("hf-mirror")
        for u in ("https://github.com/a/b", "https://civitai.com/api/x",
                  "http://127.0.0.1:8188/y", "relative/path"):
            assert mirror.convert_to_mirror(u) == u

    def test_endpoint_only_for_mirror(self, hf):
        hf("hf-mirror")
        assert mirror.hf_endpoint() == "https://hf-mirror.com"
        hf("official")
        assert mirror.hf_endpoint() == ""

    def test_preset_entry_none_for_official(self, hf):
        """官方渠道不注入 HF_ENDPOINT —— 注入了反而强制走官方"""
        hf("official")
        assert mirror.hf_preset_entry() is None

    def test_preset_entry_for_mirror(self, hf):
        hf("hf-mirror")
        e = mirror.hf_preset_entry()
        assert e == {"key": "HF_ENDPOINT", "value": "https://hf-mirror.com"}
        assert mirror.HF_ENV_KEY == "HF_ENDPOINT"

    def test_channels_cover_both_keys(self):
        keys = {c["key"] for c in mirror.HF_CHANNELS}
        assert keys == {"hf-mirror", "official"}

    def test_reexport_is_same_object(self):
        assert svc.convert_to_mirror is mirror.convert_to_mirror
        assert svc.hf_endpoint is mirror.hf_endpoint


# ============================================================
# GitHub 镜像表
# ============================================================
class TestGithubMirrors:
    def test_new_verified_candidates_present(self):
        """2026-10-18 实测可用的三个必须在线（见 mirror.py 顶部实测记录）"""
        keys = {m["key"] for m in mirror.GITHUB_MIRRORS}
        assert {"official", "ghproxy", "yuntao", "ghproxycn"} <= keys

    def test_keys_unique_and_well_formed(self):
        keys = [m["key"] for m in mirror.GITHUB_MIRRORS]
        assert len(keys) == len(set(keys)), "镜像 key 重复"
        for m in mirror.GITHUB_MIRRORS:
            assert m["label"] and m["group"]
            assert m["prefix"] == "" or m["prefix"].startswith("https://")
            # 加速网站类必须把 github.com 嵌在 prefix 里
            if m["group"] == "加速网站" and m["key"] != "official":
                assert "github.com" in m["prefix"]

    def test_official_has_empty_prefix(self):
        m = mirror._GITHUB_MIRROR_MAP["official"]
        assert m["prefix"] == ""
        assert mirror.github_mirror_prefix("official") == ""

    def test_unknown_key_returns_empty_prefix(self):
        assert mirror.github_mirror_prefix("__nope__") == ""

    def test_prefix_reads_setting(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "ghproxy"
        try:
            assert mirror.github_mirror_prefix() == \
                "https://gh-proxy.com/https://github.com/"
            assert mirror.github_mirror_label() == "gh-proxy.com"
        finally:
            svc.settings["github_mirror"] = old

    def test_apply_github_mirror(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "ghproxycn"
        try:
            got = mirror.apply_github_mirror(
                "https://github.com/ltdrdata/ComfyUI-Manager.git")
            assert got == "https://ghproxy.cn/https://github.com/" \
                          "ltdrdata/ComfyUI-Manager.git"
        finally:
            svc.settings["github_mirror"] = old

    def test_apply_github_mirror_only_github(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "ghproxy"
        try:
            u = "https://gitee.com/a/b.git"
            assert mirror.apply_github_mirror(u) == u
            # 只替换第一次出现，路径里再出现 github.com 不动
            u2 = "https://github.com/a/b/tree/main/https://github.com/c/d"
            assert mirror.apply_github_mirror(u2).count("gh-proxy.com") == 1
        finally:
            svc.settings["github_mirror"] = old

    def test_convert_git_mirror_appends_dot_git(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "yuntao"
        try:
            got = mirror.convert_git_mirror("https://github.com/a/b/")
            assert got == "https://gh-proxy.yuntao.me/https://github.com/a/b.git"
            got2 = mirror.convert_git_mirror("https://github.com/a/b.git")
            assert got2 == "https://gh-proxy.yuntao.me/https://github.com/a/b.git"
        finally:
            svc.settings["github_mirror"] = old

    def test_convert_git_mirror_non_github(self):
        assert mirror.convert_git_mirror("https://gitee.com/a/b") == \
            "https://gitee.com/a/b"


# ============================================================
# 测速自动选最快（打桩，不真的发网络请求）
# ============================================================
class TestPickFastest:
    def _stub(self, monkeypatch, results, ok=True, msg=""):
        def fake(timeout=8):
            return {"ok": ok, "msg": msg, "results": results}
        monkeypatch.setattr(mirror, "test_github_mirrors", fake)

    def test_picks_fastest_ok(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "official"
        self._stub(monkeypatch, [
            {"key": "ghproxynet", "label": "ghproxy.net", "ms": 9000, "status": "ok"},
            {"key": "ghproxycn", "label": "ghproxy.cn", "ms": 878, "status": "ok"},
            {"key": "ghproxy", "label": "gh-proxy.com", "ms": 380, "status": "ok"},
            {"key": "akams", "label": "github.akams.cn", "ms": 287, "status": "fail"},
        ])
        try:
            r = mirror.pick_fastest_mirror()
            assert r["ok"] and r["key"] == "ghproxy" and r["ms"] == 380
            assert svc.settings["github_mirror"] == "ghproxy"
        finally:
            svc.settings["github_mirror"] = old

    def test_ignores_slow(self, monkeypatch):
        """slow 不参与竞选 —— 3 秒以上的镜像 clone 插件时体感就是卡住"""
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "official"
        self._stub(monkeypatch, [
            {"key": "ghproxynet", "label": "ghproxy.net", "ms": 9000, "status": "slow"},
        ])
        try:
            r = mirror.pick_fastest_mirror()
            assert r["ok"] is False
            assert svc.settings["github_mirror"] == "official"   # 不改动
        finally:
            svc.settings["github_mirror"] = old

    def test_no_candidate_keeps_setting(self, monkeypatch):
        old = svc.settings.get("github_mirror", "official")
        svc.settings["github_mirror"] = "gitclone"
        self._stub(monkeypatch, [])
        try:
            r = mirror.pick_fastest_mirror()
            assert r["ok"] is False and "没有可用" in r["msg"]
            assert svc.settings["github_mirror"] == "gitclone"
        finally:
            svc.settings["github_mirror"] = old

    def test_probe_failure(self, monkeypatch):
        self._stub(monkeypatch, [], ok=False, msg="测试失败: boom")
        r = mirror.pick_fastest_mirror()
        assert r["ok"] is False and "boom" in r["msg"]

    def test_settings_mutated_in_place(self, monkeypatch):
        """必须是原地改 —— 重新赋值会让 services 包上的别名指向旧 dict"""
        before = id(svc.settings)
        monkeypatch.setattr(mirror, "test_github_mirrors",
                            lambda timeout=8: {"ok": True, "results": [
                                {"key": "ghproxy", "label": "gh-proxy.com",
                                 "ms": 400, "status": "ok"}]})
        old = svc.settings.get("github_mirror", "official")
        try:
            mirror.pick_fastest_mirror()
            assert id(svc.settings) == before
            assert svc.settings["github_mirror"] == "ghproxy"
            assert svc.github_mirror_prefix() != ""
        finally:
            svc.settings["github_mirror"] = old

    def test_save_failure_swallowed(self, monkeypatch):
        """save_settings 炸了不能反噬 —— 选出来的结果仍然返回。

        打桩必须打在 **`core.config.save_settings`** 上：mirror.py 是函数内
        延迟导入（`from ..config import save_settings`），每次都从 config 模块
        取最新属性。若 mirror.py 改成顶层 import，这个桩就会失效 —— 2026-10-18
        真出过这事：测试把真 settings.json 的 github_mirror 写成了 ghproxy。
        """
        import wwdm_app.core.config as cfg

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(cfg, "save_settings", boom)
        monkeypatch.setattr(mirror, "test_github_mirrors",
                            lambda timeout=8: {"ok": True, "results": [
                                {"key": "ghproxy", "label": "gh-proxy.com",
                                 "ms": 400, "status": "ok"}]})
        r = mirror.pick_fastest_mirror()
        assert r["ok"] and r["key"] == "ghproxy"

    def test_pick_fastest_does_not_touch_disk(self, monkeypatch):
        """回归护栏：选最快**绝不**写真 settings.json。

        这条是 2026-10-18 事故的永久护栏。当时 services/mirror.py 在顶层
        `from ..config import save_settings`，conftest 打在 config.save_settings
        上的桩对「模块内直接名字引用」无效 → pick_fastest_mirror() 把整份
        被 conftest 改过的内存 settings（output_dir 等指向临时目录）连名写进
        真 settings.json，指纹 adb3d977d573 -> 2877f2d99722，被指纹守卫抓出。
        """
        import wwdm_app.core.config as cfg
        calls = []
        monkeypatch.setattr(cfg, "save_settings",
                            lambda *a, **k: calls.append(1))
        monkeypatch.setattr(mirror, "test_github_mirrors",
                            lambda timeout=8: {"ok": True, "results": [
                                {"key": "ghproxy", "label": "gh-proxy.com",
                                 "ms": 400, "status": "ok"}]})
        r = mirror.pick_fastest_mirror()
        assert r["ok"] and r["key"] == "ghproxy"
        assert calls == [1], "save_settings 应被调用一次（且是 conftest 的桩）"
