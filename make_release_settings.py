#!/usr/bin/env python3
"""生成 / 体检发布包用的 settings.json（v4.1 / S6）。

用法：
    python make_release_settings.py                              # 写入 run.dist/settings.json
    python make_release_settings.py --out 某目录/settings.json     # 写入指定位置
    python make_release_settings.py --print                      # 只打印，不落盘
    python make_release_settings.py --check 某目录/settings.json   # 只体检，不改文件

发布包里的 settings.json 是要发给别人的，绝不能带本机路径和访问令牌 —— 手写手改
迟早漏一次。真正的规则在 wwdm_app/release.py（白名单 + 值级体检），这里只是入口。
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_module(path, name):
    """按文件路径加载一个模块。

    刻意不用 ``import wwdm_app.xxx`` —— 那会先把 ``wwdm_app/__init__.py`` 整条
    Flask 导入链跑一遍（app 构造、services、routes 注册……），生成一份配置文件
    没必要冒这些副作用。按文件加载则只执行目标模块本身。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载模块：%s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_defaults():
    """取 ``config.DEFAULT_SETTINGS``，且**不碰本机 settings.json**。

    ``config.py`` 末尾有一句 ``settings = load_settings()``：它会读真实
    settings.json，必要时还会**写回**。为了让打包脚本对开发者本机配置做到
    零触碰，这里先把 config.py 复制到临时目录再加载 —— 那边 ``BASE_DIR`` 落在
    临时目录，``SETTINGS_PATH`` 指向不存在的文件，于是 ``load_settings()`` 只走
    默认分支、即便写也只写临时目录（随 with 一起销毁）。

    副作用是路径类默认值会变成临时目录路径（python_path / aria2c_path /
    comfyui_dir），但它们**都不在白名单里**；万一哪天被加进白名单，
    ``release.scan_release_settings()`` 的值级体检会当场拦下来。
    """
    with tempfile.TemporaryDirectory(prefix="wwdm_release_") as td:
        pkg = os.path.join(td, "wwdm_app")
        os.makedirs(pkg)
        shutil.copy2(os.path.join(HERE, "wwdm_app", "config.py"),
                     os.path.join(pkg, "config.py"))
        cfg = _load_module(os.path.join(pkg, "config.py"), "_wwdm_release_config")
        return dict(cfg.DEFAULT_SETTINGS)


def _check(rel, path):
    """体检一份现成的 settings.json：它能不能安全地放进发布包。"""
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        print("[S6] 文件不存在：%s" % p)
        return 3
    with open(p, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        print("[S6] 不是合法 JSON：%s" % e)
        return 4
    if not isinstance(data, dict):
        print("[S6] 顶层不是对象，无法体检")
        return 4

    allowed = set(rel.RELEASE_KEYS)
    never = set(rel.RELEASE_NEVER)
    leaked = sorted(set(data) & never)                 # 直接就是「永不入包」键
    unexpected = sorted(set(data) - allowed - never)   # 白名单外的陌生键
    missing = sorted(allowed - set(data))              # 白名单里有、这份文件没有
    issues = rel.scan_release_settings(data)

    print("[S6] 体检：%s" % p)
    print("     键数 %d（白名单 %d）" % (len(data), len(allowed)))
    for it in issues:
        print("     x %s" % it)
    if leaked:
        print("     x 含「永不入包」键：%s" % "、".join(leaked))
    if unexpected:
        print("     ! 白名单外的键（若是新设置项，确认不含敏感值后再加进 RELEASE_KEYS）：%s"
              % "、".join(unexpected))
    if missing:
        print("     - 缺 %d 个白名单键（买家首启会由 DEFAULT_SETTINGS 补齐，不影响使用）：%s"
              % (len(missing), "、".join(missing)))
    if leaked or issues:
        return 2
    print("     OK 未发现本机路径 / 令牌等敏感内容")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="生成/体检发布包用的 settings.json（白名单，排除本机路径与访问令牌）")
    ap.add_argument("--out", default=os.path.join(HERE, "run.dist", "settings.json"),
                    help="输出路径（默认 run.dist/settings.json）")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="只打印内容，不写文件")
    ap.add_argument("--check", metavar="FILE",
                    help="只体检已有的 settings.json，不改任何文件")
    a = ap.parse_args(argv)

    rel = _load_module(os.path.join(HERE, "wwdm_app", "release.py"), "_wwdm_release")

    if a.check:
        return _check(rel, a.check)

    data = rel.build_release_settings(_load_defaults())
    issues = rel.scan_release_settings(data)
    if issues:
        print("[S6] 生成结果未通过体检，已拒绝写盘：")
        for it in issues:
            print("      - " + it)
        return 2

    if a.do_print:
        sys.stdout.write(rel.dump_release_settings(data))
        return 0

    out = os.path.abspath(a.out)
    parent = os.path.dirname(out)
    if parent and not os.path.isdir(parent):
        print("[S6] 输出目录不存在：%s" % parent)
        print("      （Nuitka 构建完成后才会有 run.dist/；也可以用 --out 指定别处）")
        return 3
    with open(out, "w", encoding="utf-8") as f:
        f.write(rel.dump_release_settings(data))
    print("[S6] 已写入 %s（%d 个键）" % (out, len(data)))
    print("     已强制排除：%s" % "、".join(rel.RELEASE_NEVER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
