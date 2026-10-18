
// ============================================================
// 工作流管理
// ============================================================

// 列表格式化（v6.4：此前误用了未定义的 fmtSize/fmtTime，导致整页报错）
function fmtSize(b) {
  b = Number(b) || 0;
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
  return (b / 1073741824).toFixed(1) + ' GB';
}
function fmtTime(t) {
  try {
    var d = new Date(t);
    if (isNaN(d.getTime())) return '';
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
      + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  } catch (e) { return ''; }
}

function wfInit() { wfDoBrowse(); }

function wfDoBrowse() {
  var q = document.getElementById('wfSearch');
  var s = document.getElementById('wfSort');
  var ps = document.getElementById('wfPS');
  if (q) wfState.search = q.value.trim();
  if (s) wfState.sort = s.value;
  if (ps) wfState.pageSize = parseInt(ps.value, 10) || 20;
  var params = new URLSearchParams();
  params.set('path', wfState.curPath || '');
  params.set('page', wfState.page || 1);
  params.set('page_size', wfState.pageSize || 20);
  params.set('search', wfState.search || '');
  params.set('sort', wfState.sort || 'date_desc');
  fetch('/api/workflows/browse?' + params.toString())
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { wfRender(null, d.msg); return; }
      wfRender(d);
    })
    .catch(function (e) { wfRender(null, '请求失败: ' + e); });
}

function wfRender(d, msg) {
  var list = document.getElementById('wfList');
  if (!list) return;
  if (!d) {
    list.innerHTML = '<div style="text-align:center;color:var(--text2);padding:40px">&#128194; ' + escHtml(msg || '加载失败') + '</div>';
    document.getElementById('wfPageInfo').textContent = '无内容';
    wfRenderPager(null);
    return;
  }
  wfState.totalPages = d.total_pages || 1;
  wfState.page = d.page || 1;
  // 面包屑
  var bc = document.getElementById('wfBread');
  if (bc) {
    var html = '<a href="javascript:void(0)" onclick="wfGoto(\'\')" style="color:var(--cyan)">工作流</a>';
    (d.breadcrumbs || []).forEach(function (b) {
      html += ' <span style="color:var(--text3)">/</span> <a href="javascript:void(0)" onclick="wfGoto(\'' + escAttr(b.path) + '\')" style="color:var(--cyan)">' + escHtml(b.name) + '</a>';
    });
    bc.innerHTML = html;
  }
  // 分页信息
  var pi = document.getElementById('wfPageInfo');
  if (pi) pi.textContent = d.total > 0 ? '共 ' + d.total + ' 项 | 第 ' + d.page + '/' + d.total_pages + ' 页' : '无内容';
  // 列表
  var items = d.items || [];
  if (!items.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--text2);padding:40px">&#128295; 此目录下暂无工作流文件</div>';
    wfRenderPager(d);
    return;
  }
  var html = '<div class="wf-row wf-hd"><span class="wf-col-name">名称</span><span class="wf-col-size">大小</span><span class="wf-col-date">修改时间</span><span class="wf-col-op">操作</span></div>';
  items.forEach(function (it) {
    var icon = it.is_dir ? '&#128193;' : (it.type === 'json' ? '&#128196;' : (it.type === 'image' ? '&#128444;' : '&#128196;'));
    var size = it.is_dir ? '&lt;目录&gt;' : fmtSize(it.size);
    var date = fmtTime(it.modified);
    var dpA = escAttr(it.path);
    var isd = it.is_dir ? '1' : '0';
    var opBtns = '';
    if (it.is_dir) {
      opBtns += '<button class="btn btn7" onclick="wfDelete(\'' + escAttr(it.path) + '\')" title="删除" style="font-size:12px;padding:2px 8px"><i class="bi bi-trash"></i></button>';
    } else {
      opBtns += '<button class="btn btn6" onclick="wfDownload(\'' + escAttr(it.path) + '\')" title="下载" style="font-size:12px;padding:2px 8px"><i class="bi bi-download"></i></button>'
        + ' <button class="btn btn7" onclick="wfDelete(\'' + escAttr(it.path) + '\')" title="删除" style="font-size:12px;padding:2px 8px"><i class="bi bi-trash"></i></button>'
        + (it.type === 'json' ? ' <button class="btn btn6" onclick="wfAnalyze(\'' + escAttr(it.path) + '\')" title="解析所需模型与插件" style="font-size:12px;padding:2px 8px"><i class="bi bi-search"></i></button>' : '');
    }
    html += '<div class="wf-row" data-path="' + dpA + '" data-is-dir="' + isd + '">'
      + '<span class="wf-col-name" style="cursor:pointer" onclick="' + (it.is_dir ? 'wfGoto(\'' + escAttr(it.path) + '\')' : 'wfOpen(\'' + escAttr(it.path) + '\')') + '" title="' + escAttr(it.name) + '">' + icon + ' ' + escHtml(it.name) + '</span>'
      + '<span class="wf-col-size">' + size + '</span>'
      + '<span class="wf-col-date">' + date + '</span>'
      + '<span class="wf-col-op">' + opBtns + '</span></div>';
  });
  list.innerHTML = html;
  wfRenderPager(d);
}

function wfRenderPager(d) {
  var pgEl = document.getElementById('wfPager');
  if (!pgEl) return;
  if (!d || d.total_pages <= 1) { pgEl.innerHTML = ''; return; }
  var h = '<button class="btn btn6" style="font-size:12px;padding:4px 8px"' + (d.page <= 1 ? ' disabled' : ' onclick="wfGotoPage(' + (d.page - 1) + ')"') + '><i class="bi bi-chevron-left"></i> 上一页</button>';
  for (var i = Math.max(1, d.page - 2), e = Math.min(d.total_pages, d.page + 2); i <= e; i++)
    h += '<button class="btn ' + (i === d.page ? 'btn1' : 'btn6') + '" style="font-size:12px;padding:4px 8px;min-width:32px" onclick="wfGotoPage(' + i + ')">' + i + '</button>';
  h += '<button class="btn btn6" style="font-size:12px;padding:4px 8px"' + (d.page >= d.total_pages ? ' disabled' : ' onclick="wfGotoPage(' + (d.page + 1) + ')"') + '><i class="bi bi-chevron-right"></i> 下一页</button>';
  pgEl.innerHTML = h;
}

function wfGoto(p) { wfState.curPath = p; wfState.page = 1; _selPaths.clear(); wfDoBrowse(); }
function wfGotoPage(p) { wfState.page = p; wfDoBrowse(); }
function wfClearSearch() { var q = document.getElementById('wfSearch'); if (q) q.value = ''; wfState.search = ''; wfDoBrowse(); }

function wfOpen(path) {
  fetch('/api/workflows/get?path=' + encodeURIComponent(path))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { notify(d.msg || '读取失败'); return; }
      wfState.editing = d.path;
      document.getElementById('wfEditorTitle').textContent = d.name;
      document.getElementById('wfContent').value = d.content;
      document.getElementById('wfEditorStatus').textContent = '';
      document.getElementById('wfEditorModal').style.display = 'flex';
      var ta = document.getElementById('wfContent');
      ta.focus();
      ta.scrollTop = 0;
    })
    .catch(function (e) { notify('请求失败: ' + e); });
}

function wfSave() {
  if (!wfState.editing) { notify('没有打开的文件'); return; }
  var content = document.getElementById('wfContent').value;
  // 尝试 JSON 校验
  try { JSON.parse(content); } catch (e) {
    confirmDialog('JSON 格式有误: ' + e.message + '\n仍然保存吗？',{danger:true,title:'JSON 格式错误'}).then(function(ok){
      if(!ok)return;
      doWfSave(content);
    });
    return;
  }
  doWfSave(content);
}
function doWfSave(content){
  fetch('/api/workflows/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: wfState.editing, content: content })
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.ok) {
        document.getElementById('wfEditorStatus').textContent = '已保存 ' + new Date().toLocaleTimeString();
      } else {
        notify(d.msg || '保存失败');
      }
      wfDoBrowse();
    })
    .catch(function (e) { notify('请求失败: ' + e); });
}

function wfNew() {
  promptDialog('新建工作流文件名（.json 自动补全）:','我的工作流.json',{title:'新建工作流'}).then(function(name){
    if (!name) return;
    fetch('/api/workflows/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          wfState.page = 1;
          wfDoBrowse();
          wfOpen(d.path);
        } else {
          notify(d.msg || '创建失败');
        }
      })
      .catch(function (e) { notify('请求失败: ' + e); });
  });
}

function wfDelete(path) {
  confirmDialog('确定删除该工作流？\n' + path,{danger:true}).then(function(ok){
    if (!ok) return;
    fetch('/api/workflows/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: [path] })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok && d.results && d.results[0] && d.results[0].ok) {
          wfDoBrowse();
        } else {
          notify((d.results && d.results[0] && d.results[0].msg) || '删除失败');
        }
      })
      .catch(function (e) { notify('请求失败: ' + e); });
  });
}

function wfOpenFolder() {
  fetch('/api/workflows/open_dir').then(function (r) { return r.json(); })
    .then(function (d) { if (!d.ok) notify(d.msg || '打开失败'); })
    .catch(function () {});
}

function wfDownload(path) {
  window.location.href = '/api/workflows/file?path=' + encodeURIComponent(path);
}

function wfBackup() {
  var btn = document.querySelector('button[onclick="wfBackup()"]');
  confirmDialog('将把整个工作流目录备份到已配置的「工作流备份目录」下的「年月日时分秒-备份」文件夹。\n\n请确认已在【全局设置 → 其他】中配置了备份目录。',{title:'备份工作流'}).then(function(ok){
    if (!ok) return;
    var old = '';
    if (btn) { old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 备份中...'; }
    fetch('/api/workflows/backup', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (btn) { btn.disabled = false; btn.innerHTML = old; }
        if (d.ok) { notify('\u2705 ' + d.msg + '\n' + d.dest); }
        else { notify('\u274c ' + (d.msg || '备份失败')); }
      })
      .catch(function (e) { if (btn) { btn.disabled = false; btn.innerHTML = old; } notify('请求失败: ' + e); });
  });
}

// ---- 工作流多选（Ctrl+Click，复用 _selPaths）----
(function(){
  var list = document.getElementById('wfList');
  if(!list)return;
  list.addEventListener('click', function(e){
    var el = e.target.closest('[data-path]');
    if(!el)return;
    var path = el.getAttribute('data-path');
    if(e.ctrlKey){
      e.preventDefault(); e.stopPropagation();
      if(_selPaths.has(path)){
        _selPaths.delete(path);
        el.classList.remove('selected');
      }else{
        _selPaths.add(path);
        el.classList.add('selected');
      }
    }
  }, true);
})();

// ---- 工作流右键菜单（复用全局 ctxMenu 与共享 ctx 函数，整页生效）----
(function(){
  var box = document.getElementById('page-workflows');
  if(!box)return;
  box.addEventListener('contextmenu', function(e){
    if(!e.target.closest)return;
    if(e.target.closest('input, select, textarea, button, a, label'))return;
    e.preventDefault();
    window._curPage = 'workflows';
    var el = e.target.closest('[data-path]');
    if(el)showCtxMenu(el, e.clientX, e.clientY, el.getAttribute('data-path'), el.getAttribute('data-is-dir'));
    else showCtxMenu(null, e.clientX, e.clientY, '', false);
    var sendEl = document.getElementById('ctxSendToInput');
    if(sendEl) sendEl.style.display = 'none';
  });
})();

async function wfDeleteSelected(){
  if(_selPaths.size === 0) return;
  if(!(await confirmDialog('确认删除 '+_selPaths.size+' 个文件/文件夹？\n此操作不可撤销！',{danger:true}))) return;
  var paths=[];_selPaths.forEach(function(p){paths.push(p)});
  try{
    var r=await fetch('/api/workflows/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:paths})});
    var d=await r.json();
    _selPaths.clear();
    wfDoBrowse();
  }catch(e){notify('删除失败: '+e.message)}
}


// ---- 工作流解析弹窗：模型 + 插件 ----
// 插件缺失检测为项目内置实现：数据来自 models.db 的 node_map 表（node_map 表由「插件管理 → 更新插件库」维护），不依赖是否安装 ComfyUI-Manager。
function _wfAnaChip(text, tone) {
  var c = tone === 'warn' ? 'var(--amber)' : (tone === 'ok' ? 'var(--green)' : 'var(--text2)');
  return '<span style="font-size:12px;padding:3px 10px;border-radius:20px;background:var(--card2);border:1px solid var(--border);color:' + c + '">' + text + '</span>';
}

function _wfAnaSec(text, n, miss, extra) {
  var h = '<div style="display:flex;align-items:center;gap:10px;margin:16px 2px 8px">'
    + '<span style="font-size:13px;font-weight:600;color:var(--text)">' + text + '</span>'
    + '<span style="font-size:11.5px;color:var(--text3)">共 ' + n + '</span>';
  if (miss > 0) h += '<span style="font-size:11.5px;color:var(--amber)">缺失 ' + miss + '</span>';
  else if (n > 0) h += '<span style="font-size:11.5px;color:var(--green)">已齐全</span>';
  if (extra) h += '<span style="font-size:11.5px;color:var(--text3)">' + extra + '</span>';
  h += '</div>';
  return h;
}

function _wfAnaTh(t) {
  return '<th style="padding:7px 10px;border-bottom:1px solid var(--border);font-weight:500;text-align:left;white-space:nowrap;color:var(--text3)">' + t + '</th>';
}

function _wfAnaTd(v, color) {
  return '<td style="padding:7px 10px;border-bottom:1px solid var(--border);word-break:break-all;'
    + (color ? 'color:' + color + ';' : '') + '">' + v + '</td>';
}

function _wfAnaCopyText(text, msg) {
  function done() { notify(msg); }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
  } else {
    fallbackCopy(text, done);
  }
}

function wfAnalyze(path) {
  var mt = document.getElementById('wfAnaModal');
  var bodyEl = document.getElementById('wfAnaBody');
  var nameEl = document.getElementById('wfAnaName');
  var cntEl = document.getElementById('wfAnaCount');
  if (nameEl) nameEl.textContent = '解析中…';
  if (cntEl) cntEl.textContent = '';
  if (bodyEl) bodyEl.innerHTML = '<div style="text-align:center;color:var(--text3);padding:44px 0">正在解析工作流…</div>';
  if (mt) mt.style.display = 'flex';

  fetch('/api/workflows/analyze?path=' + encodeURIComponent(path))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { wfAnaClose(); notify(d.msg || '解析失败'); return; }
      if (nameEl) nameEl.textContent = d.name || '';
      var online = !!d.comfyui_online;
      var models = d.models || [];
      var plugins = d.plugins || [];
      wfAnaModels = models;
      // 模型下载状态属于上一轮解析：清空 + 作废在途轮询，别让旧响应写进新表
      _wfAnaMdlStates = {};
      _wfAnaMdlGen++;
      if (_wfAnaMdlTimer) { clearTimeout(_wfAnaMdlTimer); _wfAnaMdlTimer = null; }
      wfAnaPlugins = plugins.filter(function (p) { return p && !p.installed; });
      // 每次解析都重置下载状态（上一次的进度不该带到这一次）
      // _idx 必须与渲染时的 plugins 下标一致 —— 轮询靠它找 wfAnaSt{i}/wfAnaDl{i}
      plugins.forEach(function (p, i) { p._dl = ''; p._idx = i; p._none = 0; p._partial = false; });
      wfAnaPluginsAll = plugins;
      // 上一轮的在途轮询必须作废，否则它的响应会往这张新表里写旧状态
      _wfAnaPollGen++;
      _wfAnaPolling = false;
      if (_wfAnaPollTimer) { clearTimeout(_wfAnaPollTimer); _wfAnaPollTimer = null; }

      var mm = d.missing_model_count || 0;
      var mp = d.missing_plugin_count || 0;
      if (cntEl) cntEl.textContent = '模型 ' + models.length + '（缺 ' + mm + '） · 插件 '
        + plugins.length + '（缺 ' + mp + '）';

      var html = '<div style="display:flex;gap:8px;flex-wrap:wrap;padding:2px 2px 6px">'
        + _wfAnaChip('需要模型 <b style="color:var(--cyan)">' + models.length + '</b> 个', '')
        + _wfAnaChip(mm > 0 ? '缺失模型 <b>' + mm + '</b> 个' : '模型已齐全', mm > 0 ? 'warn' : 'ok')
        + _wfAnaChip('需要插件 <b style="color:var(--cyan)">' + plugins.length + '</b> 个', '')
        + _wfAnaChip(mp > 0 ? '缺失插件 <b>' + mp + '</b> 个' : '插件已齐全', mp > 0 ? 'warn' : 'ok')
        + (d.builtin_node_count ? _wfAnaChip('内置节点 ' + d.builtin_node_count + ' 个', '') : '')
        + (online ? '' : _wfAnaChip('ComfyUI 未运行，无法判定插件是否已安装', 'warn'))
        + (d.lib_nodes ? '' : _wfAnaChip('本地插件库为空，请到「插件管理」点「更新插件库」', 'warn'))
        + '</div>';

      // ---------- 需要的模型 ----------
      html += _wfAnaSec('需要的模型', models.length, mm, '');
      if (!models.length) {
        html += '<div style="color:var(--text3);padding:14px 2px">未找到模型文件引用</div>';
      } else {
        html += '<table style="width:100%;border-collapse:collapse;font-size:12.5px"><thead><tr>'
          + _wfAnaTh('#') + _wfAnaTh('模型文件') + _wfAnaTh('类型') + _wfAnaTh('来源节点') + _wfAnaTh('状态') + _wfAnaTh('操作')
          + '</tr></thead><tbody>';
        models.forEach(function (m, i) {
          html += '<tr>'
            + _wfAnaTd(i + 1, 'var(--text3)')
            + _wfAnaTd('<span style="font-family:JetBrains Mono,Consolas,monospace">' + escHtml(m.file) + '</span>', '')
            + _wfAnaTd(escHtml(m.ext || ''), 'var(--text2)')
            + _wfAnaTd(escHtml(m.node_type || '') + (m.node ? ' · ' + escHtml(m.node) : ''), 'var(--text3)')
            + _wfAnaTd(m.exists
                ? '<span style="color:var(--green)">已安装</span>'
                : '<span id="wfAnaMSt' + i + '" style="color:var(--amber)">未找到</span>', '')
            + _wfAnaTd(m.exists
                ? '<span style="color:var(--text3)">—</span>'
                : '<span id="wfAnaMAct' + i + '">' + (m.id
                    ? '<button id="wfAnaMDl' + i + '" class="btn btn-teal" style="font-size:11.5px;padding:2px 9px;white-space:nowrap" onclick="wfAnaMdlDl(' + i + ',this)" title="按模型库下载地址，用 aria2 下载（可暂停/恢复/取消）"><i class="bi bi-download"></i> 下载</button>'
                    : '<button class="btn btn6" style="font-size:11.5px;padding:2px 9px;white-space:nowrap" onclick="wfAnaSearchModel(' + i + ')" title="工作流里的文件名与模型库不一致：到模型管理按名字搜索下载"><i class="bi bi-search"></i> 搜索</button>') + '</span>', '')
            + '</tr>';
        });
        html += '</tbody></table>';
        html += '<div id="wfAnaMdlNote" style="margin-top:8px;font-size:11.5px;color:var(--text3)">'
          + '「搜索」= 工作流里的模型文件名在模型库中没有匹配（常见于名称不一致）。'
          + '请点「搜索」到「模型管理」按名字查找并下载，或手动放到对应目录。'
          + '下载中的暂停 / 继续 / 取消可直接在本页操作。</div>';
      }

      // ---------- 需要的插件 ----------
      html += _wfAnaSec('需要的插件', plugins.length, mp,
        d.unknown_nodes && d.unknown_nodes.length ? '无法判定 ' + d.unknown_nodes.length + ' 个节点' : '');
      if (!plugins.length) {
        html += '<div style="color:var(--text3);padding:14px 2px">'
          + (online ? '该工作流只用到 ComfyUI 内置节点，无需额外插件' : '未取到节点注册表，无法判定插件')
          + '</div>';
      } else {
        html += '<table style="width:100%;border-collapse:collapse;font-size:12.5px"><thead><tr>'
          + _wfAnaTh('#') + _wfAnaTh('插件包') + _wfAnaTh('提供节点') + _wfAnaTh('仓库') + _wfAnaTh('状态') + _wfAnaTh('操作')
          + '</tr></thead><tbody>';
        plugins.forEach(function (p, i) {
          var nodes = (p.nodes || []).join('、');
          if ((p.nodes || []).length > 6) {
            nodes = (p.nodes || []).slice(0, 6).join('、') + ' 等 ' + p.nodes.length + ' 个';
          }
          html += '<tr>'
            + _wfAnaTd(i + 1, 'var(--text3)')
            + _wfAnaTd('<span style="font-weight:600">' + escHtml(p.pack || '') + '</span>'
                + (p.title && p.title !== p.pack ? '<div style="font-size:11.5px;color:var(--text3)">' + escHtml(p.title) + '</div>' : ''), '')
            + _wfAnaTd(escHtml(nodes), 'var(--text2)')
            + _wfAnaTd(p.repo
                ? '<a href="' + escAttr(p.repo) + '" target="_blank" style="color:var(--cyan);word-break:break-all">' + escHtml(p.repo.replace(/^https?:\/\//, '')) + '</a>'
                : '<span style="color:var(--text3)">—</span>', '')
            + _wfAnaTd(p.installed
                ? '<span style="color:var(--green)">已安装</span>'
                : '<span id="wfAnaSt' + i + '" style="color:var(--amber)">未安装</span>', '')
            + _wfAnaTd(p.installed
                ? '<span style="color:var(--text3)">—</span>'
                : '<button id="wfAnaDl' + i + '" class="btn btn-teal" style="font-size:11.5px;padding:2px 9px;white-space:nowrap" onclick="wfAnaDlClick(' + i + ',this)" title="按全局设置中的 GitHub 镜像克隆到 custom_nodes"><i class="bi bi-download"></i> 下载</button>', '')
            + '</tr>';
        });
        html += '</tbody></table>';
        html += '<div id="wfAnaDlNote" style="margin-top:8px;font-size:11.5px;color:var(--text3)"></div>';
      }

      if (d.unknown_nodes && d.unknown_nodes.length) {
        html += '<div style="margin-top:12px;padding:8px 10px;border-radius:var(--radius-sm);background:var(--card2);border:1px solid var(--border);font-size:11.5px;color:var(--text3)">'
          + '以下节点未能确定所属插件：' + escHtml(d.unknown_nodes.join('、'))
          + (online ? '' : '<br>启动 ComfyUI 后重新解析可获得更准确的判定')
          + (d.lib_nodes ? '' : '<br>本地插件库为空，请到「插件管理」页点「更新插件库」后再试')
          + '</div>';
      }

      if (bodyEl) bodyEl.innerHTML = html;
      var cm = document.getElementById('wfAnaCopyModelsBtn');
      var cp = document.getElementById('wfAnaCopyPluginsBtn');
      if (cm) cm.disabled = !models.length;
      if (cp) cp.disabled = !wfAnaPlugins.length;
      _wfAnaSyncDlAll();
      _wfAnaMdlSyncBtn();
    })
    .catch(function (e) { wfAnaClose(); notify('请求失败: ' + e); });
}

// ---- 插件下载（单个 / 批量）。镜像一律由后端按全局设置改写，前端不传 ----
function _wfAnaDlBtn(btn, state, msg) {
  if (!btn) return;
  btn.title = '';
  if (state === 'done') {
    btn.className = 'btn btn4'; btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-check2"></i> 已下载';
  } else if (state === 'cloning' || state === 'busy') {
    // 克隆中 / 启动中：这个按钮变成「停止」，随时可以停掉慢的那一个
    btn.className = 'btn btn3'; btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-stop-circle"></i> 停止';
    btn.title = msg || '停止克隆（会一并删除已克隆到一半的文件夹）';
  } else if (state === 'stopping') {
    btn.className = 'btn btn6'; btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 停止中…';
  } else if (state === 'stopped') {
    btn.className = 'btn btn6'; btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> 重新下载';
    btn.title = msg || '重新克隆这个插件';
  } else if (state === 'error') {
    btn.className = 'btn btn3'; btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> 重试';
    btn.title = msg || '';
  } else {
    btn.className = 'btn btn6'; btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + (msg || '下载中');
  }
}
async function _wfAnaStartClone(p) {
  try {
    var r;
    if (p.id) {
      r = await fetch('/api/plugins/clone/' + p.id, { method: 'POST' });
    } else {
      // 极少数仓库不在插件库列表里：后端会先补录条目拿到 id 再克隆
      r = await fetch('/api/plugins/clone_url', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: p.repo || '', name: p.pack || p.title || '' }) });
    }
    return await r.json();
  } catch (e) { return { ok: false, msg: '请求失败: ' + e }; }
}
// ---- 停止：单个 / 批量。停止后未完成的文件夹会被删掉，删不掉就提示手动删 ----
function _wfAnaActiveCount(includeStopping) {
  return (wfAnaPluginsAll || []).filter(function (p) {
    if (!p) return false;
    if (p._dl === 'cloning' || p._dl === 'busy') return true;
    return !!includeStopping && p._dl === 'stopping';
  }).length;
}
// 行内按钮两种角色：在跑就是「停止」，否则「下载」
function wfAnaDlClick(i, btn) {
  var p = wfAnaPluginsAll[i];
  if (!p) return;
  if (p._dl === 'cloning' || p._dl === 'busy' || p._dl === 'stopping') { wfAnaStop(i, btn); return; }
  wfAnaDownload(i, btn);
}
// 批量按钮两种角色：有在跑的就停止，否则下载
function wfAnaAllClick() {
  if (_wfAnaActiveCount(true)) { wfAnaStopAll(); return; }
  wfAnaDownloadAll();
}
async function _wfAnaStopOne(p) {
  try {
    var r = await fetch('/api/plugins/clone_cancel_cleanup', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: p.id }) });
    return await r.json();
  } catch (e) { return { ok: false, msg: '请求失败: ' + e }; }
}
function _wfAnaSetSt(idx, text, color) {
  var el = document.getElementById('wfAnaSt' + idx);
  if (el) el.innerHTML = '<span style="color:' + (color || 'var(--text3)') + '">' + text + '</span>';
}
// 半成品目录没能自动删掉 —— 明确提示用户先删除再重新克隆
function _wfAnaAskDelete(name, folder) {
  confirmDialog('「' + (name || '插件') + '」已停止克隆，但它的文件夹被占用，没能自动删除。\n\n'
    + (folder ? (folder + '\n\n') : '')
    + '这个目录只克隆了一半，直接重新下载会残缺或失败。\n'
    + '请先删除该插件文件夹，再点「重新下载」。',
    { title: '需要手动删除未完成的文件夹', okText: '打开插件文件夹', cancelText: '我知道了',
      icon: 'bi-exclamation-triangle-fill' })
    .then(function (go) { if (go) openPluginDir(); });
}
async function wfAnaStop(i, btn) {
  var p = wfAnaPluginsAll[i];
  if (!p) return;
  if (p._dl === 'stopping') return;
  if (p._dl !== 'cloning' && p._dl !== 'busy') return;
  var okd = await confirmDialog('停止克隆「' + (p.pack || p.title || '插件') + '」将执行两步操作：\n\n'
    + '1. 立即结束它的克隆进程（排队中的会直接从队列移除）\n'
    + '2. 删除它「克隆到一半」的文件夹\n'
    + '   （已经克隆完成的插件不会被删除）\n\n'
    + '若目录被占用删不掉，会提示你手动删除后再重新下载。是否继续？',
    { title: '停止克隆', okText: '停止并清理', danger: true, icon: 'bi-stop-circle' });
  if (!okd) return;
  p._dl = 'stopping';
  _wfAnaDlBtn(btn, 'stopping');
  _wfAnaSetSt(p._idx, '停止中…', 'var(--text3)');
  _wfAnaSyncDlAll();
  var j = await _wfAnaStopOne(p);
  p._none = 0;
  p._dl = 'stopped';
  p._partial = !!(j && j.partial);
  if (j && j.partial) _wfAnaSetSt(p._idx, '已停止 · 有残留', 'var(--red)');
  else if (j && j.removed) _wfAnaSetSt(p._idx, '已停止（已清理）', 'var(--text3)');
  else _wfAnaSetSt(p._idx, '已停止', 'var(--text3)');
  _wfAnaDlBtn(btn, 'stopped', '重新克隆');
  if (j && j.partial) {
    _wfAnaAskDelete(p.pack || p.title, j.folder);
  } else if (j && j.ok) {
    notify(j.removed ? '已停止克隆，并清理了未完成的文件夹' : j.msg);
  } else {
    notify((j && j.msg) || '停止失败');
  }
  _wfAnaSyncDlAll();
  _wfAnaPoll();
}
async function wfAnaStopAll() {
  var act = _wfAnaActiveCount(false);
  if (!act) {
    notify(_wfAnaActiveCount(true) ? '正在停止中，请稍候…' : '没有进行中的克隆任务');
    return;
  }
  var okd = await confirmDialog('停止全部克隆将执行两步操作：\n\n'
    + '1. 立即停止所有正在进行的克隆（含排队中的任务）\n'
    + '2. 删除「克隆到一半」的插件文件夹\n'
    + '   （已克隆完成的插件会保留，不会被删除）\n\n'
    + '当前有 ' + act + ' 个任务在克隆/排队。\n'
    + '若目录被占用删不掉，会提示你手动删除后再重新下载。是否继续？',
    { title: '停止全部克隆', okText: '停止并清理', danger: true, icon: 'bi-stop-circle' });
  if (!okd) return;
  var busy = (wfAnaPluginsAll || []).filter(function (p) {
    return p && (p._dl === 'cloning' || p._dl === 'busy');
  });
  busy.forEach(function (p) {
    p._dl = 'stopping';
    _wfAnaDlBtn(document.getElementById('wfAnaDl' + p._idx), 'stopping');
    _wfAnaSetSt(p._idx, '停止中…', 'var(--text3)');
  });
  _wfAnaSyncDlAll();
  var j = null;
  try {
    var r = await fetch('/api/plugins/clone_stop_all', { method: 'POST' });
    j = await r.json();
  } catch (e) { j = { ok: false, msg: '请求失败: ' + e }; }
  if (!j || !j.ok) {
    // 请求失败：状态还原成「可重新下载」，别把界面卡在「停止中」
    busy.forEach(function (p) {
      p._dl = 'stopped';
      _wfAnaDlBtn(document.getElementById('wfAnaDl' + p._idx), 'stopped', '重新克隆');
      _wfAnaSetSt(p._idx, '已停止', 'var(--text3)');
    });
    notify((j && j.msg) || '停止失败');
    _wfAnaSyncDlAll();
    _wfAnaPoll();
    return;
  }
  // 后端按 id 精确回传：removed=目录已删 / kept=克隆已完成被保留 / partial=删不掉
  var rset = {}, kset = {}, pset = {};
  (j.removed_ids || []).forEach(function (x) { rset[x] = 1; });
  (j.kept_ids || []).forEach(function (x) { kset[x] = 1; });
  (j.partial_ids || []).forEach(function (x) { pset[x] = 1; });
  var residue = [];
  busy.forEach(function (p) {
    var btn = document.getElementById('wfAnaDl' + p._idx);
    p._none = 0;
    if (kset[p.id]) {
      p._dl = 'done'; p._partial = false;
      _wfAnaSetSt(p._idx, '已下载', 'var(--green)');
      _wfAnaDlBtn(btn, 'done');
      return;
    }
    p._dl = 'stopped';
    p._partial = !!pset[p.id];
    if (p._partial) {
      _wfAnaSetSt(p._idx, '已停止 · 有残留', 'var(--red)');
      residue.push(p.pack || p.title || ('#' + p.id));
    } else {
      _wfAnaSetSt(p._idx, rset[p.id] ? '已停止（已清理）' : '已停止', 'var(--text3)');
    }
    _wfAnaDlBtn(btn, 'stopped', '重新克隆');
  });
  notify(j.msg);
  if (residue.length) {
    _wfAnaAskDelete(residue.join('、'), '');
  }
  _wfAnaSyncDlAll();
  _wfAnaPoll();
}
async function wfAnaDownload(i, btn) {
  var p = wfAnaPluginsAll[i];
  if (!p) return;
  if (p._dl === 'busy' || p._dl === 'cloning' || p._dl === 'stopping' || p._dl === 'done') return;
  if (!p.repo && !p.id) { notify('这个插件没有仓库地址，无法下载'); return; }
  if (p._partial) {
    // 上次停止时目录被占用没删掉：先提醒删干净，否则这轮克隆会残缺
    var go = await confirmDialog('「' + (p.pack || p.title || '插件') + '」上次克隆被停止，未完成的文件夹没能自动删除。\n\n请先删除该插件文件夹，再重新下载；否则这一轮克隆可能残缺。\n\n是否继续下载？',
      { title: '请先删除未完成的文件夹', okText: '继续下载', cancelText: '我先去删除' });
    if (!go) { openPluginDir(); return; }
  }
  p._dl = 'busy';
  p._none = 0;
  _wfAnaDlBtn(btn, 'busy');
  var j = await _wfAnaStartClone(p);
  if (!j || !j.ok) {
    p._dl = 'error';
    _wfAnaDlBtn(btn, 'error', j && j.msg);
    notify((j && j.msg) || '下载失败');
    _wfAnaSyncDlAll();
    return;
  }
  if (j.id) p.id = j.id;
  p._partial = false;
  p._dl = 'cloning';
  _wfAnaDlBtn(btn, 'cloning');
  notify(j.msg || '已开始下载');
  _wfAnaSyncDlAll();
  _wfAnaPoll();
}
function _wfAnaSyncDlAll() {
  var b = document.getElementById('wfAnaDownloadAllBtn');
  if (!b) return;
  var active = _wfAnaActiveCount(true);
  if (active) {
    // 有任务在跑 —— 这个按钮变成「停止全部克隆」，慢的时候用户随时能叫停
    b.className = 'btn btn3';
    b.disabled = false;
    b.innerHTML = '<i class="bi bi-stop-circle"></i> 停止全部克隆 (' + active + ')';
    b.title = '停止所有正在克隆/排队的插件，并删除克隆到一半的文件夹';
    return;
  }
  b.className = 'btn btn-teal';
  b.title = '按「全局设置 → GitHub 镜像」依次克隆所有缺失插件到 custom_nodes；并发上限取「最大同时克隆数」，超出的自动排队';
  var miss = (wfAnaPluginsAll || []).filter(function (p) { return p && !p.installed; });
  if (!miss.length) { b.disabled = true; b.innerHTML = '<i class="bi bi-download"></i> 下载缺失插件'; return; }
  var pending = miss.filter(function (p) { return p._dl !== 'done'; });
  b.disabled = !pending.length;
  b.innerHTML = pending.length
    ? '<i class="bi bi-download"></i> 下载缺失插件 (' + pending.length + ')'
    : '<i class="bi bi-check2"></i> 缺失插件已全部下载';
}
function wfAnaDownloadAll() {
  var todo = [];
  wfAnaPluginsAll.forEach(function (p, i) {
    if (p && !p.installed && p._dl !== 'done' && p._dl !== 'cloning' && p._dl !== 'busy' && p._dl !== 'stopping') todo.push(i);
  });
  if (!todo.length) { notify('没有需要下载的插件'); return; }
  confirmDialog('将按「全局设置 → GitHub 镜像」依次下载 ' + todo.length + ' 个缺失插件到 custom_nodes。\n\n• 官方直连时直接用 GitHub 地址克隆\n• 选了加速镜像时，会把仓库地址拼接镜像前缀后再克隆\n• 超出「最大同时克隆数」的会自动排队\n\n插件下载后需要重启 ComfyUI 才会生效。是否继续？',
    { title: '下载缺失插件', okText: '开始下载', icon: 'bi-download' }).then(function (ok) {
    if (!ok) return;
    (function next(k) {
      if (k >= todo.length) return;
      var i = todo[k];
      wfAnaDownload(i, document.getElementById('wfAnaDl' + i)).then(function () { next(k + 1); });
    })(0);
  });
}
function _wfAnaPoll() {
  if (_wfAnaPolling) return;                 // 已有一次在途，别叠加
  var gen = _wfAnaPollGen;
  var act = (wfAnaPluginsAll || []).filter(function (p) {
    return p && (p._dl === 'cloning' || p._dl === 'busy') && p.id;
  });
  if (!act.length) { _wfAnaPolling = false; _wfAnaDone(); return; }
  _wfAnaPolling = true;
  Promise.all(act.map(function (p) {
    return fetch('/api/plugins/clone_status/' + p.id).then(function (r) { return r.json(); })
      .then(function (st) { return { p: p, st: st }; }).catch(function () { return null; });
  })).then(function (rows) {
    _wfAnaPolling = false;
    if (gen !== _wfAnaPollGen) return;       // 期间关窗了 / 重新解析了 -> 整批丢掉，不再续期
    rows.forEach(function (row) {
      if (!row || !row.st) return;
      var p = row.p, st = row.st;
      if ((wfAnaPluginsAll || [])[p._idx] !== p) return;   // 结果属于上一轮渲染，丢掉
      var el = document.getElementById('wfAnaSt' + p._idx);
      var btn = document.getElementById('wfAnaDl' + p._idx);
      if (st.status === 'complete') {
        p._none = 0; p._dl = 'done';
        if (el) el.innerHTML = '<span style="color:var(--green)">已下载</span>';
        _wfAnaDlBtn(btn, 'done');
      } else if (st.status === 'error') {
        p._none = 0; p._dl = 'error';
        if (el) el.innerHTML = '<span style="color:var(--red)">下载失败</span>';
        _wfAnaDlBtn(btn, 'error', st.msg);
      } else if (st.status === 'none') {
        // 任务凭空消失 = 在别处被停掉了（插件页的「停止克隆」等）。
        // 连续两次才认，避开 start_clone 到线程注册之间那个极短的窗口
        p._none = (p._none || 0) + 1;
        if (p._none >= 2) {
          p._dl = 'stopped';
          if (el) el.innerHTML = '<span style="color:var(--text3)">未安装（已停止）</span>';
          _wfAnaDlBtn(btn, 'stopped', '重新克隆');
        }
      } else {
        p._none = 0;
        if (el) el.innerHTML = '<span style="color:var(--cyan)">' + escHtml(st.msg || '下载中') + '</span>';
      }
    });
    _wfAnaSyncDlAll();
    _wfAnaPollTimer = null;
    _wfAnaPoll();
  });
}
function _wfAnaDone() {
  var miss = (wfAnaPluginsAll || []).filter(function (p) { return p && !p.installed; });
  var done = miss.filter(function (p) { return p._dl === 'done'; });
  var stopped = miss.filter(function (p) { return p._dl === 'stopped'; });
  var residual = miss.filter(function (p) { return p._partial; });
  var note = document.getElementById('wfAnaDlNote');
  if (!note) return;
  if (residual.length) {
    note.innerHTML = '<i class="bi bi-exclamation-triangle"></i> <span style="color:var(--amber)">有 ' + residual.length + ' 个插件的未完成文件夹没能自动删除，请先删除该插件文件夹再重新下载</span>'
      + (done.length ? '（已下载 ' + done.length + ' 个）' : '');
    return;
  }
  if (done.length) {
    note.innerHTML = '<i class="bi bi-info-circle"></i> 已下载 ' + done.length + ' 个插件，重启 ComfyUI 后才会加载生效（可到「插件管理」查看克隆日志）';
    return;
  }
  if (stopped.length) {
    note.innerHTML = '<i class="bi bi-info-circle"></i> 已停止 ' + stopped.length + ' 个插件的克隆，未完成的文件夹已清理，可直接重新下载';
  }
}

function wfAnaCopyModels() {
  var names = (wfAnaModels || []).map(function (m) { return m.file; });
  if (!names.length) { notify('没有可复制的模型名称'); return; }
  _wfAnaCopyText(names.join('\n'), '已复制 ' + names.length + ' 个模型名称到剪贴板');
}

function wfAnaCopyPlugins() {
  var ps = wfAnaPlugins || [];
  if (!ps.length) { notify('没有缺失的插件'); return; }
  var lines = ps.map(function (p) {
    return (p.pack || '') + (p.repo ? '  ' + p.repo : '') + (p.nodes && p.nodes.length ? '  (' + p.nodes.join(', ') + ')' : '');
  });
  _wfAnaCopyText(lines.join('\n'), '已复制 ' + ps.length + ' 个缺失插件到剪贴板');
}

function fallbackCopy(text, done) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  ta.style.top = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    done();
  } catch (e) {
    notify('复制失败，请手动复制');
  }
  document.body.removeChild(ta);
}
