# -*- coding: utf-8 -*-
"""路径穿越防护 —— 这是全项目唯一一道相对路径校验，破了就是任意文件读写。

被测的三个函数在 ``wwdm_app/services/output.py``，包上再导出（``svc._safe_path``）。
这里直接打源模块测：包上别名是「再导出」而非「模块内名字引用」，打桩语义不同。
"""
import os

import pytest

from wwdm_app.services import output as out


# ============================================================
# _safe_rel：把前端传来的相对路径规范化，非法一律 None
# ============================================================
class TestSafeRel:
    def test_empty_is_root(self):
        """空串 = 根目录本身，是合法的。"""
        assert out._safe_rel("") == ""
        assert out._safe_rel(None) == ""

    @pytest.mark.parametrize("bad", [
        "../etc/passwd",         # 最经典的路径穿越
        "a/../../b",
        "..",                    # 光一个 ..
        ".. ",                   # Windows 会把末尾空格吃掉，等价于 ".."
        ".. .",                  # 同理，".. ." 也被当成 ".."
        "...",                   # 纯点，不表达任何层级
        "/abs/path",             # 绝对路径
        r"\unc\path",            # UNC / 反斜杠开头的绝对路径
        "C:\\windows",           # 盘符
        "a:C",                   # NTFS 数据流 / 驱动器相对路径
        "a\x00b",                # NUL 字节（Windows abspath 不拒绝，会漏到 open() 才炸）
    ])
    def test_rejects(self, bad):
        assert out._safe_rel(bad) is None, "应当拦下：%r" % bad

    @pytest.mark.parametrize("src,want", [
        ("a/b", "a/b"),
        ("a\\b", "a/b"),         # 反斜杠统一成正斜杠
        ("a/./b", "a/b"),        # 当前目录段被丢掉
        ("a//b", "a/b"),         # 连续分隔符
        ("a.", "a."),            # 文件名末尾带点是合法的，不该被误伤
        ("a..b", "a..b"),        # 中间的双点也不该被误伤
        ("模型/子目录", "模型/子目录"),
    ])
    def test_accepts(self, src, want):
        assert out._safe_rel(src) == want


# ============================================================
# _is_inside：跨盘符 / UNC 时 commonpath 会抛 ValueError，必须吞成 False
# ============================================================
class TestIsInside:
    def test_same_tree(self):
        assert out._is_inside("C:\\a\\b", "C:\\a\\b\\c") is True

    def test_self(self):
        assert out._is_inside("C:\\a", "C:\\a") is True

    def test_outside(self):
        assert out._is_inside("C:\\a", "C:\\b") is False

    def test_cross_drive_returns_false_not_raise(self):
        """"GET /api/output/browse?path=D:\\" 这种请求以前会 500，现在必须是 False。"""
        assert out._is_inside("C:\\a", "D:\\a\\b") is False

    def test_unc_mixed(self):
        assert out._is_inside("C:\\a", "\\\\server\\share\\x") is False

    @pytest.mark.skipif(not os.path.exists("D:\\"), reason="这台机器没有 D 盘")
    def test_real_other_drive_if_present(self):
        assert out._is_inside(os.path.abspath("C:\\"), os.path.abspath("D:\\")) is False


# ============================================================
# _safe_path：组合入口
# ============================================================
class TestSafePath:
    def test_inside(self, tmp_path):
        root = str(tmp_path)
        got = out._safe_path(root, "sub/file.txt")
        assert got == os.path.join(root, "sub", "file.txt")

    def test_root_itself(self, tmp_path):
        assert out._safe_path(str(tmp_path), "") == os.path.abspath(str(tmp_path))

    @pytest.mark.parametrize("bad", [
        "../outside",
        "a/../../etc/passwd",
        "/etc/passwd",
        "C:\\windows\\system32",
        "a\x00b",
        ".. ",
    ])
    def test_rejects_escape(self, tmp_path, bad):
        assert out._safe_path(str(tmp_path), bad) is None

    def test_symlink_escape_is_not_blocked_by_design(self, tmp_path):
        """刻意不做 realpath：junction 跨盘是本项目的受支持部署方式。

        这条不是「我们拦不住」，而是「我们决定不拦」—— 记录在此，防止后来者
        顺手加个 realpath 把正常部署弄坏。
        """
        root = os.path.join(str(tmp_path), "root")
        os.makedirs(root, exist_ok=True)
        # 只断言函数不因链接而抛异常；是否放行取决于操作系统解析结果
        try:
            out._safe_path(root, "link/inside")
        except Exception as e:  # pragma: no cover
            pytest.fail("_safe_path 不该抛异常：%r" % e)

    def test_does_not_touch_filesystem(self, tmp_path):
        """纯字符串运算，不该因为路径不存在就失败。"""
        root = os.path.join(str(tmp_path), "never_created")
        assert out._safe_path(root, "a/b/c") == os.path.join(os.path.abspath(root), "a", "b", "c")
