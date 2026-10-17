// ============================================================
// 任务中心（v5.0 第 11 步）
// ------------------------------------------------------------
// 全局俯视图：一眼看到「机器上现在有几件事在跑」。
// 与下载 / 克隆 / 修复各自的弹窗**并存** —— 抽屉看全局，弹窗看单条详情，
// 过渡期两套都在，谁都不抢谁的位置。
//
// 数据来自 GET /api/tasks（后端 core/tasks.py 的总线），刷新走公共件 Poll：
//   * 页面在后台时自动停（Poll 内建）
//   * 切回来立刻补一次，不会看到上上个周期的旧数据
//
// ⚠️ 进度为 -1 表示「后端算不出百分比」（git clone 早期、pip 安装）。
//    这时候渲染成流动条纹，**绝不能当 0%** —— 0% 看着像卡死了。
// ============================================================
(function () {
  var KIND_ICON = {
    download: 'bi-download', clone: 'bi-git', repair: 'bi-wrench',
    branches: 'bi-diagram-3', douyin: 'bi-music-note-beamed',
    comfyui: 'bi-cpu', zip: 'bi-file-zip'
  };
  var STATUS_TEXT = {
    pending: '排队中', running: '进行中', done: '完成', failed: '失败', canceled: '已取消'
  };
  // 点一行跳到对应页面（「快捷」：看到哪条异常直接过去处理）
  var KIND_PAGE = {
    download: 'models', clone: 'plugins', repair: 'repair',
    branches: 'repair', comfyui: 'launcher', douyin: 'douyin', zip: 'models'
  };
  var FINISHED = { done: 1, failed: 1, canceled: 1 };

  function _fmtAgo(ts) {
    if (!ts) return '';
    var d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (d < 5) return '刚刚';
    if (d < 60) return d + ' 秒前';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    return Math.floor(d / 3600) + ' 小时前';
  }

  function _pct(t) {
    var p = t.progress;
    if (typeof p !== 'number' || p < 0) return -1;
    return Math.max(0, Math.min(100, p));
  }

  function _itemHTML(t) {
    var active = !FINISHED[t.status];
    var p = _pct(t);
    var cls = 'tc-item' + (active ? ' is-active' : ' is-done') + (t.status === 'failed' ? ' is-failed' : '');
    var icon = KIND_ICON[t.kind] || 'bi-activity';
    var bar = (p < 0)
      ? '<div class="tc-bar unknown"><span></span></div>'
      : '<div class="tc-bar"><span style="width:' + p.toFixed(1) + '%"></span></div>';
    var right = active
      ? (p < 0 ? '进行中' : p.toFixed(0) + '%')
      : _fmtAgo(t.finished_at || t.updated_at);
    return '<div class="' + cls + '" data-kind="' + esc(t.kind) + '">'
      + '<div class="tc-row">'
      + '<span class="tc-ico"><i class="bi ' + icon + '"></i></span>'
      + '<span class="tc-name" title="' + esc(t.title || t.kind) + '">' + esc(t.title || t.id) + '</span>'
      + '<span class="tc-st ' + esc(t.status) + '">' + (STATUS_TEXT[t.status] || t.status) + '</span>'
      + '</div>'
      + (t.message ? '<div class="tc-msg">' + esc(t.message) + '</div>' : '')
      + bar
      + '<div class="tc-meta"><span>' + esc(t.id) + '</span><span>' + esc(right) + '</span></div>'
      + '</div>';
  }

  function _render(snap) {
    var list = document.getElementById('tcList');
    var badge = document.getElementById('tcBadge');
    var cnt = document.getElementById('tcCount');
    if (!list) return;

    // 角标：只数「在跑的」，已完成的不打扰
    if (badge) {
      if (snap.active > 0) { badge.style.display = ''; badge.textContent = snap.active; }
      else badge.style.display = 'none';
    }
    if (cnt) {
      cnt.textContent = snap.active > 0 ? (snap.active + ' 项进行中') : '空闲';
      cnt.className = 'tc-count' + (snap.active > 0 ? ' busy' : '');
    }

    if (!snap.tasks || !snap.tasks.length) {
      list.innerHTML = '<div class="tc-empty"><i class="bi bi-inbox"></i>'
        + '现在没有后台任务<div style="font-size:11.5px;opacity:.8">'
        + '下载模型、克隆插件、修复依赖时会自动出现在这里</div></div>';
      return;
    }
    var h = [];
    for (var i = 0; i < snap.tasks.length; i++) h.push(_itemHTML(snap.tasks[i]));
    list.innerHTML = h.join('');
  }

  async function _refresh() {
    try {
      var snap = await api('/api/tasks?limit=30');
      _render(snap);
    } catch (e) {
      // 轮询失败不弹提示：任务中心是附属面板，不该因为取不到数据就打扰用户
      try { console.warn('[taskcenter] 刷新失败', e && e.message); } catch (_) { }
    }
  }

  function _open(open) {
    var d = document.getElementById('tcDraw');
    var m = document.getElementById('tcMask');
    if (!d) return;
    d.classList.toggle('open', open);
    d.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (m) m.classList.toggle('open', open);
    if (open) _refresh();
  }

  function _toggle() {
    var d = document.getElementById('tcDraw');
    _open(d && !d.classList.contains('open'));
  }

  var TaskCenter = {
    open: function () { _open(true); },
    close: function () { _open(false); },
    toggle: _toggle,
    refresh: _refresh
  };
  window.TaskCenter = TaskCenter;

  // ---- 绑定（DOM 可能还没 ready 时先兜一手）----
  function _bind() {
    var btn = document.getElementById('fabBtnTasks');
    var close = document.getElementById('tcClose');
    var mask = document.getElementById('tcMask');
    var list = document.getElementById('tcList');
    if (btn) btn.addEventListener('click', function () { _toggle(); });
    if (close) close.addEventListener('click', function () { _open(false); });
    if (mask) mask.addEventListener('click', function () { _open(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') _open(false);
    });
    // 点一行跳到对应页面
    if (list) {
      list.addEventListener('click', function (e) {
        var row = e.target.closest ? e.target.closest('.tc-item') : null;
        if (!row) return;
        var page = KIND_PAGE[row.getAttribute('data-kind')];
        if (!page) return;
        _open(false);
        if (typeof switchPage === 'function') {
          try { switchPage(null, page); } catch (_) { }
        }
      });
    }
    // 注册轮询：1.5s。角标要实时，所以抽屉关着也刷（页面在后台时 Poll 会自动停）
    if (window.Poll) Poll.register('taskcenter', _refresh, 1500);
    else _refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _bind);
  } else {
    _bind();
  }
})();
