# -*- coding: utf-8 -*-
"""v5.1 配色主题预设（theme_preset）：字典 ↔ CSS ↔ 设置页 swatch 三处一致性 + 保存校验。

为什么要单独一个文件：新增一套配色要同时改三个地方 ——
  ① wwdm_app/core/config.py 的 THEME_PRESETS（保存时的合法值白名单）
  ② wwdm_app/static/style.css 的 light / dark 两组变量块
  ③ wwdm_app/templates/partials/settings.html 的 swatch（data-p）
漏一处就是**静默失效**：设置页能选但界面不变、或 CSS 写了却选不到，肉眼要等用户
反馈才发现。这里全盯住。

另外守三条回归：
  · 水彩硬编码不许回流改 —— 强调色字面量只能活在变量定义块里（否则换了预设，
    「文字/边框跟着预设走、底色不跟着走」会割裂）；
  · theme_preset 与 data-theme 正交 —— 明暗归 init.js 的 localStorage，配色归
    服务端设置，两条线互不覆盖；
  · 模板与 JS 拼 HTML 同样不许硬编码（第三轮迁移守卫）：onmouseover 内联样式、
    JS 拼的表格/信息条，写死水彩色一样不跟预设走；notes 配色板（用户数据）、
    B站品牌粉（外部品牌色）、swatch 圆点（展示预设自身颜色）三类刻意放行。
"""
import io
import os
import re

import pytest

import wwdm_app.services as svc
from wwdm_app.core import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(ROOT, "wwdm_app", "static", "style.css")
SETTINGS_HTML = os.path.join(ROOT, "wwdm_app", "templates", "partials", "settings.html")
TEMPLATE_DIR = os.path.join(ROOT, "wwdm_app", "templates")
JS_DIR = os.path.join(ROOT, "wwdm_app", "static", "js")

# 每个预设块必须给齐的变量。缺一个就会出现「部分表面跟随预设、部分不跟随」的割裂，
# 而且这种割裂在 watercolor 下完全看不出来（基座本来就有一套），只有换预设才暴露。
PRESET_VARS = ("--cyan", "--cyan2", "--purple", "--purple2", "--blue",
               "--teal", "--teal2", "--amber", "--amber2",
               "--glow-cyan", "--glow-purple", "--glow-card")

# 水彩/默认调的字面量。这些只允许出现在 :root 变量定义块里；别处再出现就等于
# 「这块表面不跟随配色预设」。
ACCENT_LITERALS = (
    "rgba(74,111,165", "rgba(195,141,148", "rgba(143,179,224", "rgba(216,163,170",
    "rgba(212,163,115", "rgba(176,123,130", "rgba(127,182,168", "rgba(224,185,143",
    "#4a6fa5", "#8fb3e0", "#d8a3aa", "#d4a373", "#7fb6a8", "#5f9a8c", "#e0b98f",
)


def _css_text():
    return io.open(CSS_PATH, "rb").read().decode("utf-8")


def _css_no_comments():
    return re.sub(r"/\*.*?\*/", "", _css_text(), flags=re.S)


def _var_blocks(selector):
    """取 `selector{...}` 块内容列表（按花括号配平切，支持一行多块）。"""
    css = _css_text()
    out = []
    for m in re.finditer(re.escape(selector) + r"\{", css):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(css)):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[i + 1:j])
                    break
    return out


def _strip_var_blocks(css):
    """删掉所有 :root（含带属性选择器的）变量定义块，只剩普通规则。"""
    out, i = [], 0
    pat = re.compile(r":root(?:\[[^\]]*\])*\{")
    while True:
        m = pat.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        j, depth = m.end() - 1, 0
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1
    return "".join(out)


@pytest.fixture(autouse=True)
def _snapshot_settings():
    """整字典快照还原。

    POST /api/settings 对缺失的 comfyui_dir 等键会回填 DEFAULT_SETTINGS 真实路径
    （blueprints/settings.py 的 `if not d.get(...)` 系列），污染 conftest 沙箱后会
    带挂 test_safety_net。口径同 tests/test_audit.py / test_mirror.py。
    """
    old = dict(svc.settings)
    yield
    svc.settings.clear()
    svc.settings.update(old)


# ============================================================
# ① 字典 ↔ CSS
# ============================================================
class TestDictCssConsistency:
    def test_every_preset_has_css_blocks(self):
        css_keys = set(re.findall(r':root\[data-preset="(\w+)"\]\{', _css_text()))
        # watercolor 不写块：:root 基座就是它（见 style.css 顶部注释）
        assert css_keys == set(config.THEME_PRESETS) - {"watercolor"}, \
            "CSS 预设块与 THEME_PRESETS 不一致：css=%s dict=%s" % (
                sorted(css_keys), sorted(config.THEME_PRESETS))

    def test_watercolor_has_no_block(self):
        """默认配色靠 :root 基座；真写了块反而会按源码顺序压过 dark 基底。"""
        assert ':root[data-preset="watercolor"]' not in _css_text()

    @pytest.mark.parametrize("key", sorted(set(config.THEME_PRESETS) - {"watercolor"}))
    def test_each_preset_has_light_and_dark(self, key):
        assert _var_blocks(':root[data-preset="%s"]' % key), \
            "%s 缺日间变量块" % key
        assert _var_blocks(':root[data-theme="dark"][data-preset="%s"]' % key), \
            "%s 缺夜间变量块（与 data-theme 正交，两个维度都要给值）" % key

    @pytest.mark.parametrize("key", sorted(set(config.THEME_PRESETS) - {"watercolor"}))
    def test_each_preset_defines_all_vars(self, key):
        for sel in (':root[data-preset="%s"]' % key,
                    ':root[data-theme="dark"][data-preset="%s"]' % key):
            body = _var_blocks(sel)[0]
            for v in PRESET_VARS:
                assert v + ":" in body, "%s 块里少了 %s" % (sel, v)

    def test_preset_specificity_beats_dark_base(self):
        """dark 变体必须写成 [data-theme="dark"][data-preset=x]（0,3,0），
        只写 [data-preset=x]（0,2,0）会与 dark 基底同特异性、按源码顺序决定胜负。"""
        for key in config.THEME_PRESETS:
            if key == "watercolor":
                continue
            assert (':root[data-theme="dark"][data-preset="%s"]{' % key) in _css_text()


# ============================================================
# ② 字典 ↔ 设置页 swatch
# ============================================================
class TestSwatchConsistency:
    def _html(self):
        return io.open(SETTINGS_HTML, "rb").read().decode("utf-8")

    def test_chip_for_every_preset(self):
        chips = set(re.findall(r'data-p="(\w+)"', self._html()))
        assert chips == set(config.THEME_PRESETS), \
            "设置页 swatch 与 THEME_PRESETS 不一致：html=%s dict=%s" % (
                sorted(chips), sorted(config.THEME_PRESETS))

    def test_chip_calls_pick_preset(self):
        """swatch 走 onclick="pickPreset(this)"（普通 script，函数必须挂全局）。"""
        html = self._html()
        assert html.count('onclick="pickPreset(this)"') == len(config.THEME_PRESETS)

    def test_wallpaper_rows_live_in_appearance_group(self):
        """壁纸两行归「外观主题」sg（v5.1 从「其他」迁入），别又散回去。"""
        html = self._html()
        i_appearance = html.index('sg-title">外观主题')
        i_other = html.index('sg-title">其他')
        assert html.index('id="swall"') > i_appearance and html.index('id="swall"') < i_other
        assert html.index('id="swallo"') > i_appearance and html.index('id="swallo"') < i_other


# ============================================================
# ③ 硬编码不许回流
# ============================================================
class TestNoHardcodedAccent:
    def test_accent_literals_only_in_var_blocks(self):
        css = _strip_var_blocks(_css_no_comments())
        hits = [lit for lit in ACCENT_LITERALS if lit in css]
        assert not hits, \
            "这些强调色字面量又出现在变量块外面，换了预设它们不会跟着变：%s" % hits

    def test_border_derives_from_cyan(self):
        """--border/--border2 由 --cyan 经 color-mix 派生，预设才带得动边框。"""
        css = _css_text()
        assert re.search(r"--border:color-mix\(in srgb,var\(--cyan\)", css)


# ============================================================
# ⑥ 模板与 JS 拼 HTML 同样不许硬编码回流（第三轮迁移守卫）
# ============================================================
class TestNoHardcodedAccentInTemplates:
    """style.css 变量化只是第一战场。

    模板内联防（onmouseover 里的 this.style、拼在一行里的 background）和 JS 拼
    HTML 字符串（镜像测速表头、预览信息条）**同样不跟 data-preset 走** —— 这些
    地方写死水彩色，换预设时就是「文字边框跟得走、底色跟不走」的割裂。v5.1 第三轮
    已把 about/help 两页 + 预览信息条 + 镜像/审计表头共 52 处变量化，这里守住别回流。
    """

    # 行级放行。这些行的水彩色是「用户数据 / 外部品牌色 / 预设自身展示」，
    # 刻意不跟随预设（每条理由见右侧注释；新增放行必须连理由一起加）：
    ALLOW_SUBSTR = (
        "preset-chip",       # settings.html swatch：圆点展示的是预设自身颜色
        "251,114,153",       # help.html 视频教程卡：B站品牌粉 #fb7299，外部品牌色
    )

    def _iter_files(self):
        for base in (TEMPLATE_DIR, JS_DIR):
            for dirpath, _dirs, files in os.walk(base):
                for fn in sorted(files):
                    if fn.endswith((".html", ".js")):
                        yield os.path.join(dirpath, fn)

    def test_no_accent_literals_outside_allowlist(self):
        bad = []
        for path in self._iter_files():
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            with io.open(path, "rb") as f:
                text = f.read().decode("utf-8")
            for n, line in enumerate(text.splitlines(), 1):
                if not any(lit in line for lit in ACCENT_LITERALS):
                    continue
                if any(sub in line for sub in self.ALLOW_SUBSTR):
                    continue
                bad.append("%s:%d" % (rel, n))
        assert not bad, \
            ("这些模板/JS 行又写死了水彩强调色，换了预设它们不跟着变：%s\n"
             "（如果是新增的合理场景——用户数据/外部品牌色——请连同理由加进 "
             "ALLOW_SUBSTR）" % bad)

    def test_allowlist_entries_are_live(self):
        """allowlist 每条都要真的在拦东西。

        否则某天 notes 配色板被变量化了，allowlist 条目还挂着，测试会在一片
        不存在的地方放行 —— 绿灯但毫无意义。
        """
        all_text = ""
        for path in self._iter_files():
            with io.open(path, "rb") as f:
                all_text += f.read().decode("utf-8")
        dead = [sub for sub in self.ALLOW_SUBSTR if sub not in all_text]
        assert not dead, "allowlist 里有已不存在的条目，请删掉：%s" % dead


# ============================================================
# ⑦ 保存校验（与 github_mirror / hf_channel 同一套口径）
# ============================================================
class TestSettingsApi:
    def test_default_is_watercolor(self):
        assert config.DEFAULT_SETTINGS["theme_preset"] == "watercolor"

    def test_theme_preset_known(self):
        assert config.theme_preset_known("watercolor")
        assert config.theme_preset_known("mono")
        assert not config.theme_preset_known("neon")
        assert not config.theme_preset_known("")

    def test_valid_preset_saved(self, client, origin):
        r = client.post("/api/settings", headers=origin,
                        json={"theme_preset": "forest"})
        assert r.get_json()["ok"]
        assert svc.settings["theme_preset"] == "forest"

    def test_invalid_preset_falls_back_to_watercolor(self, client, origin):
        svc.settings["theme_preset"] = "violet"
        r = client.post("/api/settings", headers=origin,
                        json={"theme_preset": "neon"})
        assert r.get_json()["ok"]
        assert svc.settings["theme_preset"] == "watercolor"

    def test_missing_key_keeps_current(self, client, origin):
        """别的测试/前端只发部分字段时，不许把用户的配色冲掉。"""
        svc.settings["theme_preset"] = "sunset"
        r = client.post("/api/settings", headers=origin,
                        json={"hf_channel": "hf-mirror"})
        assert r.get_json()["ok"]
        assert svc.settings["theme_preset"] == "sunset"

    def test_get_returns_preset(self, client):
        assert "theme_preset" in client.get("/api/settings").get_json()


# ============================================================
# ⑤ 服务端直出 + 发布包白名单
# ============================================================
class TestServerSideRender:
    def test_index_carries_data_preset(self, client):
        """data-preset 服务端直出：配色是首屏视觉的一部分，等前端 fetch 完再套色
        会先闪一下默认水彩。"""
        html = client.get("/").get_data(as_text=True)
        want = svc.settings.get("theme_preset", "watercolor")
        assert 'data-preset="%s"' % want in html

    def test_index_preset_follows_setting(self, client):
        svc.settings["theme_preset"] = "mono"
        html = client.get("/").get_data(as_text=True)
        assert 'data-preset="mono"' in html


class TestReleaseWhitelist:
    def test_in_release_keys(self):
        from wwdm_app import release
        assert "theme_preset" in release.RELEASE_KEYS

    def test_not_blanked(self):
        """不是本机路径/令牌，发布包里带着用户的配色选择即可（默认 watercolor）。"""
        from wwdm_app import release
        assert "theme_preset" not in release.RELEASE_BLANK
