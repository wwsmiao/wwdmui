# -*- coding: utf-8 -*-
"""pytest 公共夹具 —— 三条安全网全开。

顺序很重要，别乱调：

  1. **先**记下三个真库/配置文件的 sha256 指纹（此时还没 import 业务模块）
  2. 把 models.db / notes.db 复制到临时目录，把 ``core.db`` 的路径指过去
  3. 打桩 ``config.save_settings``，避免内存里的配置被写回磁盘
  4. 把 output / input / workflow / comfyui 目录也指到临时目录
  5. 打桩危险动作（关机 / 启动 ComfyUI / aria2 / 克隆 / 修复子进程）
  6. 会话结束时比对指纹，任何一个变了就 FAIL —— 「测试弄脏了真数据」必须当场暴露

⚠️ 为什么指纹要在 import 之前取：``wwdm_app/__init__.py`` 末尾有
``app = create_app()`` 兼容层，**导入这个包就会建一个应用**。先取指纹才能连
这一步一起盯住 —— 哪天有人在工厂里加了写库逻辑，这条守卫会立刻叫。

⚠️ 打桩位置要看清是「包属性访问」还是「模块内直接名字引用」：后者必须打在
源模块上，打在包上不生效（2026-10-17 真库被插 probe 记录就是这么来的）。
"""
import hashlib
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------
# ① 指纹基线 —— 必须在 import 业务模块之前
# ---------------------------------------------------------------
WATCHED = {
    "models.db": os.path.join(ROOT, "wwdm_app", "static", "info", "models.db"),
    "notes.db": os.path.join(ROOT, "wwdm_app", "static", "info", "notes.db"),
    "settings.json": os.path.join(ROOT, "settings.json"),
}


def _sha(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


FINGERPRINT_BEFORE = {k: _sha(v) for k, v in WATCHED.items()}

# ---------------------------------------------------------------
# ② 数据库副本 —— 也必须在业务模块真正被用到之前
# ---------------------------------------------------------------
from wwdm_app.core import config  # noqa: E402
from wwdm_app.core import db as coredb  # noqa: E402

# ⚠️ settings.json 要在 import 之后**重新记基线**（只重记这一个文件）：
# ``load_settings()`` 在 core/config.py 导入期就会 repair —— 补默认键、
# token_required bool 化、max_upload_gb clamp、web_port 越界修正、
# access_token 空则生成 —— 然后 ``_save_settings(merged)`` 落盘。这是
# 既有设计行为（每次启动管理器都会做），不是测试搞脏数据。
# 若不重记，每次往 DEFAULT_SETTINGS 加新键（v5.1 加 hf_env_inject 时就
# 踩过），第一次跑 pytest 必然被指纹守卫误报「测试弄脏了真数据」。
# models.db / notes.db 没有导入期写盘行为，仍按 import 前的基线盯。
FINGERPRINT_BEFORE["settings.json"] = _sha(WATCHED["settings.json"])

TMP_ROOT = tempfile.mkdtemp(prefix="wwdm_pytest_")

for _attr, _name in (("DB_PATH", "models.db"), ("NOTES_DB_PATH", "notes.db")):
    _src = getattr(coredb, _attr)
    _dst = os.path.join(TMP_ROOT, _name)
    shutil.copy2(_src, _dst)
    setattr(coredb, _attr, _dst)

# ---------------------------------------------------------------
# ③ 配置：目录指临时，保存动作打桩（settings 只能原地改，不能重新赋值）
# ---------------------------------------------------------------
for _k in ("output_dir", "input_dir", "workflow_dir", "comfyui_dir"):
    if _k in config.settings:
        _d = os.path.join(TMP_ROOT, _k)
        os.makedirs(_d, exist_ok=True)
        config.settings[_k] = _d

config.save_settings = lambda *a, **k: None

# ---------------------------------------------------------------
# ④ 夹具
# ---------------------------------------------------------------
from wwdm_app import create_app  # noqa: E402

_ORIGIN = {"Origin": "http://localhost"}   # test_client 下同源头要这么写


@pytest.fixture(scope="session", autouse=True)
def _fingerprint_guard():
    """会话级守卫：真库 / 配置文件的内容一个字节都不许变。"""
    yield
    dirty = {}
    for k, path in WATCHED.items():
        before, after = FINGERPRINT_BEFORE[k], _sha(path)
        if before != after:
            dirty[k] = "%s -> %s" % (before, after)
    assert not dirty, "❌ 测试弄脏了真数据：%s" % dirty


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    """会话结束删掉临时目录（DB 副本 + 重定向的 output/input/workflow）。"""
    yield
    try:
        shutil.rmtree(TMP_ROOT, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _stub_dangerous():
    """危险动作一律打桩：关机 / 启停 ComfyUI / aria2 / 克隆线程 / 修复子进程。"""
    import wwdm_app.services as svc

    names = ("schedule_shutdown", "cancel_shutdown", "start_clone",
             "_git_clone_thread", "aria2_start", "aria2_stop",
             "start_comfyui", "stop_comfyui", "run_repair_action",
             "kill_aria2_processes", "cleanup_all")
    saved = {}
    for n in names:
        if hasattr(svc, n):
            saved[n] = getattr(svc, n)
            # ⚠️ 默认参数绑定是必须的：直接写 ``lambda *a: ("stubbed", n)`` 会踩
            # Python 的**闭包晚绑定** —— 所有桩捕获的都是同一个 n，最后全指向
            # 循环最后一个名字。桩看着生效，标记却全错，极易埋雷。
            setattr(svc, n, (lambda *a, _n=n, **k: ("stubbed", _n)))

    # ---- save_settings 通用打桩 ----
    # 上面只打了 ``config.save_settings``，但「模块内直接名字引用」拿不到它 ——
    # 2026-10-18 真出过事：services/mirror.py 顶层
    # ``from ..config import save_settings``，pick_fastest_mirror() 于是把整份
    # 被 conftest 改过的内存 settings（output_dir/input_dir/workflow_dir 指向
    # 临时目录 + github_mirror=ghproxy）连名写进**真** settings.json，
    # 指纹 adb3d977d573 -> 2877f2d99722，被 _fingerprint_guard 当场抓出。
    # 这里遍历所有已导入的 wwdm_app 模块，把各自的 save_settings 一并打桩，
    # 无论新代码用哪种导入写法都逃不掉。
    import sys as _sys
    _saved_save = {}
    for _mname, _mod in list(_sys.modules.items()):
        if not _mname.startswith("wwdm_app") or _mod is None:
            continue
        if hasattr(_mod, "save_settings"):
            _saved_save[_mname] = _mod.save_settings
            try:
                _mod.save_settings = lambda *a, **k: None
            except Exception:
                pass
    yield
    for n, fn in saved.items():
        setattr(svc, n, fn)
    for _mname, _fn in _saved_save.items():
        _mod = _sys.modules.get(_mname)
        if _mod is not None:
            try:
                _mod.save_settings = _fn
            except Exception:
                pass


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture
def client(app):
    """Flask test_client。同源校验要求 Origin 与 host 一致。"""
    return app.test_client()


@pytest.fixture
def origin():
    """写请求要带的同源头。"""
    return dict(_ORIGIN)


@pytest.fixture(autouse=True)
def _tasks_clean():
    """每个用例前后清空任务总线，保证用例之间互不串场。"""
    from wwdm_app.core import tasks
    tasks.reset()
    yield
    tasks.reset()


@pytest.fixture
def seed_tasks():
    """造一组标准任务：2 条在跑（running/pending）+ 4 条终态（2 failed / done / canceled）。

    返回 seed() 可重造。
    """
    from wwdm_app.core import tasks as T

    def seed():
        T.reset()
        T.upsert("download:run", "download", title="正在下载的模型", status=T.RUNNING)
        T.upsert("download:pend", "download", title="排队中的下载", status=T.PENDING)
        T.upsert("download:f1", "download", title="下载失败 1", status=T.FAILED)
        T.upsert("clone:f2", "clone", title="克隆失败 2", status=T.FAILED)
        T.upsert("repair:d1", "repair", title="修复成功", status=T.DONE)
        T.upsert("repair:cc", "repair", title="修复已取消", status=T.CANCELED)
        return T

    return seed
