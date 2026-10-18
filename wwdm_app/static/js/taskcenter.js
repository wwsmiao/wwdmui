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
    branches: 'bi-diagram-3',
    comfyui: 'bi-cpu', zip: 'bi-file-zip'
  };
  var STATUS_TEXT = {
    pending: '排队中', running: '进行中', done: '完成', failed: '失败', canceled: '已取消'
  };
  // 点一行跳到对应页面（「快捷」：看到哪条异常直接过去处理）
  var KIND_PAGE = {
    download: 'models', clone: 'plugins', repair: 'repair',
    branches: 'repair', comfyui: 'launcher', zip: 'models'
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
    // 只有**终态**才给删除按钮。在跑的任务删掉也没用：后台线程不会因此停下，
    // 下一个心跳上报又把条目原样重建出来，用户只会觉得「清了又冒出来」。
    var del = active ? '' :
      '<button type="button" class="tc-del" data-act="del" data-id="' + esc(t.id) + '"'
      + ' title="移除这条记录"><i class="bi bi-x-lg"></i></button>';
    return '<div class="' + cls + '" data-kind="' + esc(t.kind) + '" data-id="' + esc(t.id) + '">'
      + '<div class="tc-row">'
      + '<span class="tc-ico"><i class="bi ' + icon + '"></i></span>'
      + '<span class="tc-name" title="' + esc(t.title || t.kind) + '">' + esc(t.title || t.id) + '</span>'
      + '<span class="tc-st ' + esc(t.status) + '">' + (STATUS_TEXT[t.status] || t.status) + '</span>'
      + del
      + '</div>'
      + (t.message ? '<div class="tc-msg">' + esc(t.message) + '</div>' : '')
      + bar
      + '<div class="tc-meta"><span>' + esc(t.id) + '</span><span>' + esc(right) + '</span></div>'
      + '</div>';
  }

  // ---- 手动清理区：有已结束的记录才出现，空抽屉底部不挂无用按钮 ----
  function _renderActions(tasks) {
    var box = document.getElementById('tcActions');
    if (!box) return;
    var failedN = 0, finishN = 0;
    for (var k = 0; k < tasks.length; k++) {
      if (!FINISHED[tasks[k].status]) continue;
      finishN++;
      if (tasks[k].status === 'failed') failedN++;
    }
    if (!finishN) { box.style.display = 'none'; return; }
    box.style.display = '';

    var bf = document.getElementById('tcClearFailed');
    var bn = document.getElementById('tcClearFinished');
    if (bf) {
      bf.style.display = failedN ? '' : 'none';
      bf.textContent = '清除失败 ' + failedN;
      bf.setAttribute('data-n', failedN);
    }
    if (bn) {
      bn.textContent = '清除已结束 ' + finishN;
      bn.setAttribute('data-n', finishN);
    }
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

    var tasks = snap.tasks || [];
    _renderActions(tasks);

    if (!tasks.length) {
      list.innerHTML = '<div class="tc-empty"><i class="bi bi-inbox"></i>'
        + '现在没有后台任务<div style="font-size:11.5px;opacity:.8">'
        + '下载模型、克隆插件、修复依赖时会自动出现在这里</div></div>';
      return;
    }
    var h = [];
    for (var i = 0; i < tasks.length; i++) h.push(_itemHTML(tasks[i]));
    list.innerHTML = h.join('');
  }

  // ---------------- 手动清理 ----------------
  // 后端 DELETE /api/tasks 只清终态，撞上「还在跑」的会计入 skipped 返回，
  // 这里据此明确告诉用户「有 N 项没动」，避免他以为清失败了。
  async function _clear(opt) {
    opt = opt || {};
    var ids = (opt.ids && opt.ids.length) ? opt.ids : null;
    var url = '/api/tasks?' + (ids
      ? ('id=' + encodeURIComponent(ids.join(',')))
      : ('scope=' + encodeURIComponent(opt.scope || 'finished')));
    try {
      var r = await api(url, { method: 'DELETE' });
      var done = (r && r.removed) || 0;
      var skip = (r && r.skipped) || 0;      // 点名要删却在跑、被拒绝的
      var left = (r && r.running) || 0;      // 还剩多少在跑
      if (!opt.quiet) {
        if (!done && !skip) notify('没有可清除的记录', 'info');
        else if (left) notify('已清除 ' + done + ' 条；还有 ' + left + ' 项在进行中，已保留', 'info');
        else notify('已清除 ' + done + ' 条记录', 'success');
        if (skip) notify(skip + ' 项还在进行中，清不掉 —— 要中止请到对应页面操作', 'info');
      }
    } catch (e) {
      notify('清除失败：' + ((e && e.message) || e), 'error');
    }
    _refresh();
  }

  function _askClear(btn, scope, what) {
    if (!btn) return;
    var n = parseInt(btn.getAttribute('data-n'), 10) || 0;
    confirmDialog(
      '确定清除 ' + n + ' 条' + what + '吗？\n这只是把记录从列表里移除，不会影响正在跑的任务。',
      { title: '清除' + what, okText: '清除', danger: true }
    ).then(function (yes) { if (yes) return _clear({ scope: scope }); })
      .catch(function (e) { try { console.warn('[taskcenter] 清除异常', e); } catch (_) { } });
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
    refresh: _refresh,
    clear: _clear
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
    // 点一行跳到对应页面；点行尾的 × 则是删掉这条记录
    if (list) {
      list.addEventListener('click', function (e) {
        if (!e.target.closest) return;
        var delBtn = e.target.closest('.tc-del');
        if (delBtn) {
          e.stopPropagation();
          var tid = delBtn.getAttribute('data-id');
          // 单条删除不弹提示：条目当下就消失了，再飘一条通知是多余的噪音
          if (tid) _clear({ ids: [tid], quiet: true });
          return;
        }
        var row = e.target.closest('.tc-item');
        if (!row) return;
        var page = KIND_PAGE[row.getAttribute('data-kind')];
        if (!page) return;
        _open(false);
        if (typeof switchPage === 'function') {
          try { switchPage(null, page); } catch (_) { }
        }
      });
    }
    // 底部批量清理
    var cf = document.getElementById('tcClearFailed');
    var cn = document.getElementById('tcClearFinished');
    if (cf) cf.addEventListener('click', function () { _askClear(cf, 'failed', '失败任务'); });
    if (cn) cn.addEventListener('click', function () { _askClear(cn, 'finished', '已结束任务'); });
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
