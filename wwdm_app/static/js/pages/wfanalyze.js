
// ============================================================
// 解析结果：缺失模型一键下载
// 与「下载缺失插件」对称 —— 能单独下、也能一键下全部，进度直接显示在行内。
// 复用「模型管理」页那套 aria2 流程（/api/download/start|batch + all_status），
// 所以只有「模型库里查得到记录（有下载地址）」的缺失模型才给按钮，其余显示「库中无」。
// ============================================================
var _wfAnaMdlTimer = null, _wfAnaMdlGen = 0, _wfAnaMdlStates = {};
var _WFANA_MDL_END = { complete: 1, error: 1, removed: 1 };

function _wfAnaMdlTargets() {
  return (wfAnaModels || []).filter(function (m) { return m && !m.exists && m.id; });
}
function _wfAnaMdlSet(i, html) {
  var el = document.getElementById('wfAnaMSt' + i);
  if (el) el.innerHTML = html;
}
function _wfAnaMdlPending() {
  return _wfAnaMdlTargets().filter(function (m) {
    var s = _wfAnaMdlStates[m.id];
    return !s || !_WFANA_MDL_END[s.status];
  });
}
function _wfAnaMdlActHtml(i, label, fn, cls) {
  return '<button class="btn ' + cls + '" style="font-size:11px;padding:1px 8px;margin-right:4px" onclick="' + fn + '(' + i + ')">' + label + '</button>';
}
function _wfAnaMdlDlHtml(i) {
  return '<button id="wfAnaMDl' + i + '" class="btn btn-teal" style="font-size:11.5px;padding:2px 9px;white-space:nowrap" onclick="wfAnaMdlDl(' + i + ',this)" title="按模型库下载地址，用 aria2 下载"><i class="bi bi-download"></i> 下载</button>';
}
function _wfAnaMdlSearchHtml(i) {
  return '<button class="btn btn6" style="font-size:11.5px;padding:2px 9px;white-space:nowrap" onclick="wfAnaSearchModel(' + i + ')" title="工作流里的文件名与模型库不一致：到模型管理按名字搜索下载"><i class="bi bi-search"></i> 搜索</button>';
}
function _wfAnaMdlRender() {
  (wfAnaModels || []).forEach(function (m, i) {
    if (!m || m.exists) return;
    var stEl = document.getElementById('wfAnaMSt' + i);
    var acEl = document.getElementById('wfAnaMAct' + i);
    if (!m.id) {
      if (stEl) stEl.innerHTML = '<span style="color:var(--amber)">库中无</span>';
      if (acEl) acEl.innerHTML = _wfAnaMdlSearchHtml(i);
      return;
    }
    var s = _wfAnaMdlStates[m.id];
    if (!s) {
      if (stEl) stEl.innerHTML = '<span style="color:var(--amber)">未找到</span>';
      if (acEl) acEl.innerHTML = _wfAnaMdlDlHtml(i);
      return;
    }
    var head = s.status === 'complete'
      ? '<span style="color:var(--green)">已完成</span>'
      : dlBadge(s.status);
    var pct = s.progress || 0;
    if (stEl) stEl.innerHTML = head + (pct > 0 ? ' ' + renderMiniBar(pct) : '');
    if (!acEl) return;
    if (_WFANA_MDL_END[s.status]) {
      acEl.innerHTML = '<button class="btn btn-teal" style="font-size:11.5px;padding:2px 9px" onclick="wfAnaMdlDl(' + i + ',this)"><i class="bi bi-arrow-clockwise"></i> 重试</button>';
    } else if (s.status === 'paused') {
      acEl.innerHTML = _wfAnaMdlActHtml(i, '继续', 'wfAnaMdlResume', 'btn6')
        + _wfAnaMdlActHtml(i, '取消', 'wfAnaMdlCancel', 'btn3');
    } else if (s.status === 'active') {
      acEl.innerHTML = _wfAnaMdlActHtml(i, '暂停', 'wfAnaMdlPause', 'btn6')
        + _wfAnaMdlActHtml(i, '取消', 'wfAnaMdlCancel', 'btn3');
    } else {
      acEl.innerHTML = _wfAnaMdlActHtml(i, '取消', 'wfAnaMdlCancel', 'btn3');
    }
  });
  _wfAnaMdlSyncBtn();
}
async function wfAnaMdlPause(i) {
  var m = (wfAnaModels || [])[i];
  if (!m || !m.id) return;
  try {
    await fetch('/api/download/pause/' + m.id, { method: 'POST' });
    if (_wfAnaMdlStates[m.id]) _wfAnaMdlStates[m.id].status = 'paused';
    _wfAnaMdlRender();
  } catch (e) { notify('暂停失败: ' + e.message); }
}
async function wfAnaMdlResume(i) {
  var m = (wfAnaModels || [])[i];
  if (!m || !m.id) return;
  try {
    await fetch('/api/download/resume/' + m.id, { method: 'POST' });
    if (_wfAnaMdlStates[m.id]) _wfAnaMdlStates[m.id].status = 'active';
    _wfAnaMdlRender();
    _wfAnaMdlPoll();
  } catch (e) { notify('继续失败: ' + e.message); }
}
async function wfAnaMdlCancel(i) {
  var m = (wfAnaModels || [])[i];
  if (!m || !m.id) return;
  var st = _wfAnaMdlStates[m.id];
  if (!st) return;
  if (!(await confirmDialog('确认取消「' + m.file + '」的下载？'))) return;
  var queued = (st.status === 'queue' || st.status === 'waiting');
  try {
    await fetch('/api/download/' + (queued ? 'cancel_queue' : 'cancel') + '/' + m.id,
      { method: 'POST' });
    delete _wfAnaMdlStates[m.id];
    _wfAnaMdlRender();
  } catch (e) { notify('取消失败: ' + e.message); }
}
function wfAnaSearchModel(i) {
  var m = (wfAnaModels || [])[i];
  if (!m) return;
  var name = String(m.file || '').replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
  try { wfAnaClose(); } catch (e) {}
  var inp = document.getElementById('sinput');
  if (inp) inp.value = name;
  try { mPage = 1; } catch (e) {}
  try { switchPage(null, 'models'); } catch (e) {}  // 内部会 loadModels()
}
function _wfAnaMdlSyncBtn() {
  var b = document.getElementById('wfAnaModelDlAllBtn');
  if (!b) return;
  var all = _wfAnaMdlTargets();
  if (!all.length) {
    b.disabled = true;
    b.innerHTML = '<i class="bi bi-download"></i> 下载缺失模型';
    b.title = '模型库里没有可下载的缺失模型记录（可到「模型管理」页导入，或手动补下载地址）';
    return;
  }
  b.title = '按模型库里的下载地址，用 aria2 依次下载所有缺失模型；并发上限取「最大同时下载数」，超出的自动排队';
  var pending = _wfAnaMdlPending();
  b.disabled = !pending.length;
  b.innerHTML = pending.length
    ? '<i class="bi bi-download"></i> 下载缺失模型 (' + pending.length + ')'
    : '<i class="bi bi-check2"></i> 缺失模型已全部下载';
}
async function wfAnaMdlDl(i, btn) {
  var m = (wfAnaModels || [])[i];
  if (!m || !m.id) return;
  if (!await checkA2()) { notify('请先启动 aria2c'); return; }
  if (btn) btn.disabled = true;
  try {
    var r = await fetch('/api/download/start/' + m.id, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mirror: false }) });
    var j = await r.json();
    if (!j.ok) { notify(j.msg || '下载失败'); if (btn) btn.disabled = false; return; }
    var queued = String(j.msg || '').indexOf('队列') >= 0;
    _wfAnaMdlStates[m.id] = { status: queued ? 'queue' : 'waiting', progress: 0 };
    if (btn) btn.style.display = 'none';
    _wfAnaMdlRender();
    _wfAnaMdlPoll();
  } catch (e) {
    notify('下载失败: ' + e.message);
    if (btn) btn.disabled = false;
  }
}
function wfAnaMdlAllClick() {
  var todo = _wfAnaMdlPending();
  if (!todo.length) { notify('没有需要下载的缺失模型'); return; }
  confirmDialog('将用 aria2 下载 ' + todo.length + ' 个缺失模型到模型库记录里的保存路径。\n\n'
    + '• 地址取自「模型管理」页里该模型的下载地址\n'
    + '• 超出「最大同时下载数」的会自动排队\n'
    + '• 进度可在本窗口行内查看，暂停 / 恢复 / 取消到「模型管理」页操作\n\n'
    + '是否继续？', { title: '下载缺失模型', okText: '开始下载', icon: 'bi-download' })
    .then(function (ok) { if (ok) wfAnaMdlAll(); });
}
async function wfAnaMdlAll() {
  if (!await checkA2()) { notify('请先启动 aria2c'); return; }
  var ids = _wfAnaMdlPending().map(function (m) { return m.id; });
  if (!ids.length) { notify('没有可下载的缺失模型'); return; }
  try {
    var r = await fetch('/api/download/batch', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: ids, mirror: false }) });
    var j = await r.json();
    if (!j.ok) { notify('批量下载失败'); return; }
    var started = 0, queued = 0, bad = 0;
    (j.results || []).forEach(function (x) {
      if (!x.ok) bad++;
      else if (String(x.msg || '').indexOf('队列') >= 0) queued++;
      else started++;
    });
    (wfAnaModels || []).forEach(function (m) {
      if (!m || m.exists || !m.id || ids.indexOf(m.id) < 0) return;
      _wfAnaMdlStates[m.id] = { status: 'queue', progress: 0 };
    });
    _wfAnaMdlRender();
    notify('缺失模型：开始 ' + started + ' 个，排队 ' + queued + ' 个' + (bad ? '，失败 ' + bad + ' 个' : ''));
    _wfAnaMdlPoll();
  } catch (e) { notify('批量下载失败: ' + e.message); }
}
function _wfAnaMdlPoll() {
  if (_wfAnaMdlTimer) return;                 // 已有一次在途，别叠加
  _wfAnaMdlTick(++_wfAnaMdlGen);
}
async function _wfAnaMdlTick(gen) {
  if (gen !== _wfAnaMdlGen) return;
  try {
    var r = await fetch('/api/download/all_status');
    var d = await r.json();
    var seen = {};
    (d.active || []).forEach(function (x) {
      if (!(x.id in _wfAnaMdlStates)) return;   // 只跟踪本弹窗发起的，避免误改别的任务
      seen[x.id] = 1;
      _wfAnaMdlStates[x.id] = { status: x.status || 'active', progress: x.progress || 0 };
    });
    (d.queue || []).forEach(function (x) {
      var cur = _wfAnaMdlStates[x.id];
      if (!cur || _WFANA_MDL_END[cur.status]) return;
      seen[x.id] = 1;
      _wfAnaMdlStates[x.id] = { status: 'queue', progress: 0 };
    });
    // 曾发给它、现在既不在 active 也不在 queue -> 视为结束（与「模型管理」页的判定一致）
    Object.keys(_wfAnaMdlStates).forEach(function (k) {
      var s = _wfAnaMdlStates[k];
      if (s && !_WFANA_MDL_END[s.status] && !seen[k]) {
        _wfAnaMdlStates[k] = { status: 'complete', progress: 100 };
      }
    });
    _wfAnaMdlRender();
  } catch (e) { /* 网络抖动：下一轮再取 */ }
  if (gen !== _wfAnaMdlGen) return;
  var alive = (wfAnaModels || []).some(function (m) {
    var s = m && _wfAnaMdlStates[m.id];
    return !!(s && !_WFANA_MDL_END[s.status]);
  });
  var box = document.getElementById('wfAnaModal');
  var open = box && box.style.display !== 'none';
  if (gen === _wfAnaMdlGen && alive && open) {
    _wfAnaMdlTimer = setTimeout(function () { _wfAnaMdlTick(gen); }, 2000);
  } else {
    _wfAnaMdlTimer = null;
  }
}

function wfAnaClose() {
  var m = document.getElementById('wfAnaModal');
  if (m) m.style.display = 'none';
  // 关窗就把在途的轮询作废，否则请求回来还会接着续期（原本 _wfAnaPollTimer 从没赋过值）
  _wfAnaPollGen++;
  _wfAnaPolling = false;
  if (_wfAnaPollTimer) { clearTimeout(_wfAnaPollTimer); _wfAnaPollTimer = null; }
  // 模型下载的轮询同理作废
  _wfAnaMdlGen++;
  if (_wfAnaMdlTimer) { clearTimeout(_wfAnaMdlTimer); _wfAnaMdlTimer = null; }
}

function wfCloseEditor() {
  var ta = document.getElementById('wfContent');
  var dirty = false;
  if (ta && wfState.editing) {
    // 简单脏检查：保存后 status 显示时间，编辑则清空
    dirty = document.getElementById('wfEditorStatus').textContent !== '';
  }
  document.getElementById('wfEditorModal').style.display = 'none';
  wfState.editing = null;
}

function wfFormatJson() {
  var ta = document.getElementById('wfContent');
  if (!ta) return;
  try {
    var obj = JSON.parse(ta.value);
    ta.value = JSON.stringify(obj, null, 2);
    document.getElementById('wfEditorStatus').textContent = '已格式化';
  } catch (e) {
    notify('JSON 格式错误: ' + e.message);
  }
}

// 编辑器 Ctrl+S
document.addEventListener('keydown', function (e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    var m = document.getElementById('wfEditorModal');
    if (m && m.style.display !== 'none') {
      e.preventDefault();
      wfSave();
    }
  }
  if (e.key === 'Escape') {
    var m2 = document.getElementById('wfEditorModal');
    if (m2 && m2.style.display !== 'none') { wfCloseEditor(); return; }
    var ma = document.getElementById('wfAnaModal');
    if (ma && ma.style.display !== 'none') { wfAnaClose(); }
  }
});
