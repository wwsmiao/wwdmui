
// ============================================================
// 本地文件浏览引擎：模型管理（ComfyUI/models）与插件管理（ComfyUI/custom_nodes）共用
// 每个页面一个实例（元素 id 用各自前缀），后端按 scope 区分根目录
// ============================================================
function mlEsc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function mlBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  if (n < 1099511627776) return (n / 1073741824).toFixed(2) + ' GB';
  return (n / 1099511627776).toFixed(2) + ' TB';
}
function mlTime(ts) {
  if (!ts) return '-';
  var d = new Date(ts * 1000);
  function p(x) { return (x < 10 ? '0' : '') + x }
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
// 打开右键菜单后的短暂保护期：忽略期间发生的 scroll（多为浏览器自动滚动/重排引起）
var mlCtxGuardUntil = 0;

function mkLocalView(cfg) {
  var P = cfg.prefix, SCOPE = cfg.scope;
  var SW = cfg.sw, DB = cfg.db, LO = cfg.local;
  var SORT_KEY = cfg.sortKey || ('lvSort_' + SCOPE);
  var S = {
    mode: 'db', cur: '', parent: null, foldersRaw: [], files: [], deep: null,
    sel: new Set(), rootName: cfg.rootName,
    stat: { free: 0, stats: null, deep: '' }, statSeq: 0
  };

  function el(k) { return document.getElementById(P + k) }
  function qVal() { var e = el('Search'); return e ? String(e.value || '').trim() : '' }
  function deepOn() { var e = el('Deep'); return !!(e && e.checked) }
  function sortVal() { var e = el('Sort'); return e ? e.value : 'name_asc' }
  function lower(s) { return String(s == null ? '' : s).toLowerCase() }
  function isDir(f) { return !!(f && f.is_dir) }
  function qs(extra) { return 'scope=' + encodeURIComponent(SCOPE) + (extra ? '&' + extra : '') }
  function getJSON(path, extra) { return fetch(path + '?' + qs(extra)).then(function (r) { return r.json() }) }
  function postJSON(path, body) {
    body = body || {}; body.scope = SCOPE;
    return fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json() });
  }

  // ---------------- 模式切换 ----------------
  function setMode(m, keep) {
    loadSort();
    S.mode = (m === 'local') ? 'local' : 'db';
    try { localStorage.setItem(cfg.store, S.mode) } catch (e) { }
    var sw = document.getElementById(SW);
    if (sw) {
      var btns = sw.querySelectorAll('.ms-btn');
      for (var i = 0; i < btns.length; i++) {
        btns[i].classList.toggle('active', btns[i].getAttribute('data-mode') === S.mode);
      }
    }
    var dbBox = document.getElementById(DB), loBox = document.getElementById(LO);
    if (dbBox) dbBox.style.display = (S.mode === 'db') ? '' : 'none';
    if (loBox) loBox.style.display = (S.mode === 'local') ? '' : 'none';
    if (S.mode === 'local') { if (!keep) refresh() } else { ctxHide() }
  }
  function onEnter() {
    var saved = null;
    try { saved = localStorage.getItem(cfg.store) } catch (e) { }
    setMode(saved === 'local' ? 'local' : 'db', false);
  }

  // ---------------- 排序（记忆在浏览器，按页面分开存） ----------------
  function sortChanged() {
    try { localStorage.setItem(SORT_KEY, sortVal()) } catch (e) { }
    refresh();
  }
  function loadSort() {
    var v = null;
    try { v = localStorage.getItem(SORT_KEY) } catch (e) { }
    var e = el('Sort');
    if (!e || !v) return;
    for (var i = 0; i < e.options.length; i++) {
      if (e.options[i].value === v) { e.value = v; return }
    }
  }

  // ---------------- 视图计算 ----------------
  function pickFolders() {
    if (SCOPE !== 'plugins' || S.deep) return S.foldersRaw;
    var q = lower(qVal());
    if (!q) return S.foldersRaw;
    return S.foldersRaw.filter(function (f) { return lower(f.name).indexOf(q) >= 0 });
  }
  function visibleFiles() {
    if (S.deep) return S.deep.items || [];
    var q = lower(qVal());
    if (!q || deepOn()) return S.files;
    return S.files.filter(function (f) { return lower(f.name).indexOf(q) >= 0 });
  }
  function emptyWord() { return SCOPE === 'plugins' ? '插件或文件' : '文件' }

  function paintStat() {
    var e = el('Stat');
    if (!e) return;
    var tip;
    if (S.deep) {
      e.textContent = S.stat.deep;
      tip = '递归搜索：在 ' + (S.cur || S.rootName) + ' 下所有子目录里按名称匹配';
    } else {
      var t = [pickFolders().length + ' 个子文件夹'];
      var st = S.stat.stats;
      if (st) {
        t.push('共 ' + Number(st.files || 0) + ' 个文件');
        t.push(mlBytes(st.size || 0));
      } else {
        t.push('统计中…');
      }
      t.push('磁盘可用 ' + mlBytes(S.stat.free));
      e.textContent = t.join(' · ');
      tip = '当前目录：' + (S.cur || S.rootName) +
        '\n子文件夹 = 当前目录的直接子文件夹数（同列表中标「文件夹」的行数）' +
        '\n文件数与占用 = 当前目录下所有层级的合计';
    }
    e.title = tip;
  }

  // ---------------- 目录浏览 ----------------
  function go(rel) {
    S.sel.clear();
    S.deep = null;
    var d = el('Deep'); if (d) d.checked = false;
    var s = el('Search'); if (s) s.value = '';
    S.cur = rel || '';
    refresh();
  }
  function up() { if (S.parent === null || S.parent === undefined) return; go(S.parent) }

  async function refresh() {
    if (deepOn() && qVal()) return searchNow();
    S.deep = null;
    var j;
    try {
      j = await getJSON('/api/models/local/list',
        'rel=' + encodeURIComponent(S.cur) + '&sort=' + encodeURIComponent(sortVal()));
    } catch (e) { notify('读取失败: ' + e.message); return }
    if (!j.ok) {
      S.foldersRaw = []; S.files = []; S.parent = null; S.stat.stats = null; S.stat.deep = '';
      var tb0 = el('Tbody');
      if (tb0) tb0.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--red);padding:24px">' + mlEsc(j.msg || '读取失败') + '</td></tr>';
      var c0 = el('TableCard'); if (c0) c0.style.display = '';
      var st0 = el('Stat'); if (st0) { st0.textContent = ''; st0.title = '' }
      renderCrumbs([{ name: S.rootName, rel: '' }]);
      updateSel();
      return;
    }
    S.foldersRaw = j.folders || [];
    S.files = j.files || [];
    S.parent = j.parent;
    S.cur = j.rel || '';
    if (j.crumbs && j.crumbs.length) S.rootName = j.crumbs[0].name || S.rootName;
    S.stat.free = j.disk_free || 0;
    S.stat.stats = j.stats || null;
    var ub = el('UpBtn'); if (ub) ub.disabled = (S.cur === '');
    renderCrumbs(j.crumbs || [{ name: S.rootName, rel: '' }]);
    renderFiles(); paintStat(); updateSel();
    loadStats();
  }

  // 递归统计较慢（插件目录可能上万文件），列表先出、数字后补
  async function loadStats() {
    if (S.deep) return;
    var want = S.cur, seq = ++S.statSeq;
    var j;
    try { j = await getJSON('/api/models/local/stats', 'rel=' + encodeURIComponent(want)) }
    catch (e) { return }
    if (seq !== S.statSeq || want !== S.cur || S.deep) return;
    if (!j || !j.ok) return;
    S.stat.stats = j;
    var kids = j.kids || {};
    S.foldersRaw.forEach(function (f) {
      var b = kids[f.name];
      if (b) { f.size = b.size; f.files = b.files; f.dirs = b.dirs; f.rec = true }
    });
    renderFiles(); paintStat();
  }

  function renderCrumbs(crumbs) {
    var box = el('Crumbs');
    if (!box) return;
    box.innerHTML = '';
    crumbs.forEach(function (c, i) {
      if (i > 0) {
        var sep = document.createElement('span');
        sep.className = 'cbs'; sep.textContent = '/';
        box.appendChild(sep);
      }
      var last = (i === crumbs.length - 1);
      var e = document.createElement('span');
      e.className = 'cbc' + (last ? ' cur' : '') + (i === 0 ? ' cbroot' : '');
      if (i === 0) {
        var ic = document.createElement('i');
        ic.className = 'bi bi-hdd-stack';
        e.appendChild(ic);
        e.appendChild(document.createTextNode(' '));
      }
      var nm = document.createElement('span');
      nm.textContent = c.name || S.rootName;
      e.appendChild(nm);
      if (!last) e.addEventListener('click', function () { go(c.rel) });
      box.appendChild(e);
    });
  }

  // 列表数据：子文件夹排在文件前面（递归搜索时结果自身已含文件夹，不再另加）
  function listItems() {
    if (S.deep) return visibleFiles();
    return pickFolders().concat(visibleFiles());
  }

  // 文件夹没有单一"大小"，显示递归合计并在后面缀文件数
  function folderSizeCell(f) {
    if (f.files === null || f.files === undefined) return '<span class="mldim">统计中…</span>';
    var tip = f.files + ' 个文件' + (f.dirs ? ' · ' + f.dirs + ' 个子目录' : '');
    return '<span title="' + mlEsc(tip) + '">' + mlBytes(f.size || 0) + '</span>' +
      '<span class="mlsub">' + f.files + ' 个文件</span>';
  }

  function renderFiles() {
    var tb = el('Tbody');
    if (!tb) return;
    var card = el('TableCard');
    var folders = S.deep ? [] : pickFolders();
    var files = visibleFiles().slice();
    var q = qVal();
    var filtering = !!S.deep || !!q;
    // 递归搜索时文件夹结果排在前面（插件目录里搜的主要是插件）
    if (S.deep && files.length) {
      var hasDir = false;
      for (var i = 0; i < files.length; i++) { if (isDir(files[i])) { hasDir = true; break } }
      if (hasDir) files.sort(function (a, b) { return (isDir(b) ? 1 : 0) - (isDir(a) ? 1 : 0) });
    }
    // 当前目录既无子文件夹也无文件（且不是搜索过滤）时不显示表格，避免残留表头
    if (!folders.length && !files.length && !filtering) {
      if (card) card.style.display = 'none';
      tb.innerHTML = '';
      return;
    }
    if (card) card.style.display = '';
    var curDir = S.cur || S.rootName;
    if (!folders.length && !files.length) {
      tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:24px">没有匹配的' + emptyWord() + '</td></tr>';
      return;
    }
    var html = folders.map(function (f) {
      var checked = S.sel.has(f.rel) ? ' checked' : '';
      var git = (SCOPE === 'plugins' && f.git) ? ' <span class="mltag-git">git</span>' : '';
      return '<tr data-rel="' + mlEsc(f.rel) + '" data-kind="folder" title="右键可重命名 / 移动 / 删除">' +
        '<td class="cb-cell"><input type="checkbox" class="mlcb" data-rel="' + mlEsc(f.rel) + '"' + checked + '></td>' +
        '<td><i class="bi bi-folder-fill mlico-dir"></i>' + mlEsc(f.name) + git + '</td>' +
        '<td><span class="mltag mltag-dir">文件夹</span></td>' +
        '<td>' + folderSizeCell(f) + '</td>' +
        '<td class="mldim">' + (f.mtime ? mlTime(f.mtime) : '-') + '</td>' +
        '<td class="mldim">' + mlEsc(curDir) + '</td>' +
        '</tr>';
    }).join('');
    html += files.map(function (f) {
      var dir = S.deep ? (f.dir || (S.rootName + ' 根目录')) : curDir;
      var checked = S.sel.has(f.rel) ? ' checked' : '';
      var kind = isDir(f) ? 'folder' : 'file';
      var typeCell = isDir(f)
        ? '<span class="mltag mltag-dir">文件夹</span>'
        : '<span class="mltag ' + (f.is_model ? 'mltag-model' : 'mltag-other') + '">' + mlEsc(f.ext || '文件') + '</span>';
      var nameCell = isDir(f)
        ? '<i class="bi bi-folder-fill mlico-dir"></i>' + mlEsc(f.name)
        : mlEsc(f.name);
      return '<tr data-rel="' + mlEsc(f.rel) + '" data-kind="' + kind + '" title="右键可重命名 / 移动 / 删除">' +
        '<td class="cb-cell"><input type="checkbox" class="mlcb" data-rel="' + mlEsc(f.rel) + '"' + checked + '></td>' +
        '<td>' + nameCell + '</td>' +
        '<td>' + typeCell + '</td>' +
        '<td>' + (isDir(f) ? '-' : mlBytes(f.size)) + '</td>' +
        '<td class="mldim">' + mlTime(f.mtime) + '</td>' +
        '<td class="mldim">' + mlEsc(dir) + '</td>' +
        '</tr>';
    }).join('');
    tb.innerHTML = html;
  }

  // 搜索框输入：纯前端过滤当前目录（插件目录连文件夹卡片一起过滤）
  function filterLocal() {
    if (deepOn()) return;
    S.deep = null;
    renderFiles(); paintStat(); updateSel();
  }

  function updateSel() {
    var n = S.sel.size;
    var c = el('SelCnt');
    if (c) c.textContent = n;
    var sa = el('SelAll');
    if (sa) {
      var total = listItems().length;
      sa.checked = (total > 0 && n >= total);
      sa.indeterminate = (n > 0 && n < total);
    }
  }
  function setSel(rel, on) { if (on) { S.sel.add(rel) } else { S.sel.delete(rel) } updateSel() }
  function toggleAll(c) {
    var list = listItems();
    if (c) { list.forEach(function (f) { S.sel.add(f.rel) }) } else { S.sel.clear() }
    renderFiles();
    updateSel();
  }

  // ---------------- 搜索 ----------------
  async function searchNow() {
    var q = qVal();
    if (!q) { S.deep = null; return refresh() }
    if (!deepOn()) { return filterLocal() }
    var j;
    try {
      j = await getJSON('/api/models/local/search', 'q=' + encodeURIComponent(q) +
        '&rel=' + encodeURIComponent(S.cur) + '&sort=' + encodeURIComponent(sortVal()));
    } catch (e) { notify('搜索失败: ' + e.message); return }
    if (!j.ok) { notify(j.msg || '搜索失败'); return }
    S.deep = { items: j.items || [], q: q };
    S.stat.deep = '递归搜索「' + q + '」命中 ' + S.deep.items.length + ' 个' +
      (SCOPE === 'plugins' ? '结果' : '文件') + (j.truncated ? '（已截断）' : '');
    renderFiles(); paintStat(); updateSel();
  }

  // ---------------- 打开 / 定位 ----------------
  function openDir() {
    getJSON('/api/models/local/open', 'rel=' + encodeURIComponent(S.cur))
      .then(function (j) { if (!j.ok) notify(j.msg) }).catch(function () { });
  }
  function reveal(rel) {
    getJSON('/api/models/local/open', 'rel=' + encodeURIComponent(rel) + '&select=1')
      .then(function (j) { if (!j.ok) notify(j.msg) }).catch(function () { });
  }

  // ---------------- 重命名 / 移动 / 删除 ----------------
  async function renameOne(rel) {
    var old = String(rel).split('/').pop();
    var nv = await promptDialog('重命名「' + old + '」为：', old, { title: '重命名', okText: '确定' });
    if (nv === null || nv === undefined) return;
    nv = String(nv).trim();
    if (!nv || nv === old) return;
    try {
      var j = await postJSON('/api/models/local/rename', { rel: rel, new_name: nv });
      if (!j.ok) { notify(j.msg || '重命名失败'); return }
      notify(j.msg);
      S.sel.delete(rel);
      refresh();
    } catch (e) { notify('重命名失败: ' + e.message) }
  }

  function pickDest(count) {
    return new Promise(function (resolve) {
      var mask = document.createElement('div');
      mask.className = 'cdlg-mask';
      mask.innerHTML = '<div class="cdlg cdlg-wide">' +
        '<div class="cdlg-hd"><span class="cdlg-ico"><i class="bi bi-arrows-move"></i></span><h3>移动到…</h3></div>' +
        '<div class="cdlg-body">共 ' + count + ' 项将被移动，请选择 ' + mlEsc(S.rootName) + ' 下的目标文件夹：</div>' +
        '<div class="cdlg-input-wrap"><input class="cdlg-input" type="text" placeholder="输入关键字过滤文件夹…"></div>' +
        '<div class="mldestlist gscroll"></div>' +
        '<div class="cdlg-ft"><button class="btn btn6 cdlg-cancel">取消</button></div></div>';
      document.body.appendChild(mask);
      var listBox = mask.querySelector('.mldestlist');
      var input = mask.querySelector('.cdlg-input');
      var items = [];
      function done(v) { mask.remove(); document.removeEventListener('keydown', onKey); resolve(v) }
      function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); done(null) } }
      function draw() {
        var q = lower(input.value);
        var hit = items.filter(function (it) { return !q || lower(it.rel).indexOf(q) >= 0 });
        listBox.innerHTML = hit.length
          ? hit.map(function (it) {
            return '<div class="mldest" data-rel="' + mlEsc(it.rel) + '" style="padding-left:' +
              (11 + (it.depth || 0) * 16) + 'px">' +
              '<i class="bi bi-folder-fill"></i><span>' + mlEsc(it.rel || (S.rootName + '（根目录）')) + '</span></div>';
          }).join('')
          : '<div class="mldest-empty">没有匹配的文件夹</div>';
      }
      listBox.addEventListener('click', function (e) {
        var e2 = e.target.closest ? e.target.closest('.mldest') : null;
        if (e2) done(e2.getAttribute('data-rel'));
      });
      input.addEventListener('input', draw);
      mask.querySelector('.cdlg-cancel').addEventListener('click', function () { done(null) });
      mask.addEventListener('click', function (e) { if (e.target === mask) done(null) });
      document.addEventListener('keydown', onKey);
      getJSON('/api/models/local/move_targets').then(function (j) {
        items = j.items || [];
        draw();
      }).catch(function () { listBox.innerHTML = '<div class="mldest-empty">读取文件夹失败</div>' });
      input.focus();
    });
  }

  async function moveRels(rels) {
    if (!rels || !rels.length) { notify('请先勾选要移动的项目'); return }
    var dest = await pickDest(rels.length);
    if (dest === null) return;
    try {
      var j = await postJSON('/api/models/local/move', { rels: rels, dest: dest });
      if (!j.ok) { notify(j.msg || '移动失败') }
      else { notify(j.msg); rels.forEach(function (x) { S.sel.delete(x) }) }
      refresh();
    } catch (e) { notify('移动失败: ' + e.message) }
  }

  async function deleteRels(rels) {
    if (!rels || !rels.length) { notify('请先勾选要删除的项目'); return }
    var names = rels.slice(0, 6).map(function (x) { return '· ' + x }).join('\n');
    if (rels.length > 6) names += '\n… 以及另外 ' + (rels.length - 6) + ' 项';
    var ok = await confirmDialog(
      '以下 ' + rels.length + ' 项将被移入系统回收站（可从回收站还原）：\n\n' + names,
      { title: '删除确认', okText: '移入回收站', danger: true });
    if (!ok) return;
    try {
      var j = await postJSON('/api/models/local/delete', { rels: rels });
      if (!j.ok) { notify(j.msg || '删除失败'); return }
      notify(j.msg);
      rels.forEach(function (x) { S.sel.delete(x) });
      refresh();
    } catch (e) { notify('删除失败: ' + e.message) }
  }

  // ---------------- 上传文件（右键菜单入口，支持单个 / 多个） ----------------
  var _upInput = null, _upDirInput = null, _upDest = '';

  function mkUpInput(dirMode) {
    var i = document.createElement('input');
    i.type = 'file';
    i.multiple = true;
    i.id = P + (dirMode ? 'UpDirInput' : 'UpInput');
    i.style.display = 'none';
    if (dirMode) { i.webkitdirectory = true; i.setAttribute('webkitdirectory', ''); }
    i.addEventListener('change', function () {
      // 注意：FileList 是活的，i.value='' 会把它一起清空，
      // 必须先拷成真数组再清 value，否则上传会被静默跳过。
      var fs = i.files ? Array.prototype.slice.call(i.files) : [];
      var rels = [];
      if (dirMode) {
        for (var k = 0; k < fs.length; k++) rels.push(fs[k].webkitRelativePath || fs[k].name);
      }
      i.value = '';                 // 清空，保证同一个文件还能再次触发 change
      if (fs.length) doUpload(fs, rels);
    });
    document.body.appendChild(i);
    return i;
  }
  function ensureUpInput() {
    if (!_upInput) _upInput = mkUpInput(false);
    return _upInput;
  }
  function ensureUpDirInput() {
    if (!_upDirInput) _upDirInput = mkUpInput(true);
    return _upDirInput;
  }

  function pickUpload(destRel) {
    _upDest = destRel || '';
    ensureUpInput().click();
  }
  function pickUploadFolder(destRel) {
    _upDest = destRel || '';
    ensureUpDirInput().click();
  }

  // 进度提示：模型文件动辄几百 MB，用 XHR 才能拿到 upload.onprogress
  function upToast(text) {
    var t = notify(text, 'info', 1800000);
    return {
      set: function (s) { var b = t && t.querySelector('.ntf-body'); if (b) b.textContent = s },
      close: function () { var x = t && t.querySelector('.ntf-x'); if (x) x.click() }
    };
  }

  function doUpload(files, relPaths) {
    var dest = _upDest || '';
    var n = files.length;
    var fd = new FormData();
    for (var i = 0; i < n; i++) fd.append('files', files[i]);
    fd.append('dest', dest);
    fd.append('scope', SCOPE);
    for (var r = 0; r < (relPaths || []).length; r++) fd.append('paths', relPaths[r]);
    var t = upToast('正在上传 ' + n + ' 个文件…');
    var x = new XMLHttpRequest();
    x.open('POST', '/api/models/local/upload');
    x.upload.onprogress = function (e) {
      if (e && e.lengthComputable && e.total) {
        t.set('正在上传 ' + n + ' 个文件… ' + Math.round(e.loaded * 100 / e.total) + '%');
      }
    };
    x.onload = function () {
      t.close();
      var j = null;
      try { j = JSON.parse(x.responseText) } catch (err) { }
      if (!j) { notify('上传失败：服务端返回异常（HTTP ' + x.status + '）'); return }
      if (!j.ok) { notify(j.msg || '上传失败'); return }
      notify(j.msg || ('已上传 ' + n + ' 个文件'));
      S.sel.clear();
      refresh();
    };
    x.onerror = function () { t.close(); notify('上传失败：连接中断') };
    x.onabort = function () { t.close(); notify('上传已取消') };
    x.send(fd);
  }

  // ---------------- 右键菜单（增删改查入口） ----------------
  function kindOf(rel) {
    for (var i = 0; i < S.foldersRaw.length; i++) {
      if (S.foldersRaw[i].rel === rel) return 'folder';
    }
    if (S.deep) {
      var arr = S.deep.items || [];
      for (var j = 0; j < arr.length; j++) {
        if (arr[j].rel === rel) return isDir(arr[j]) ? 'folder' : 'file';
      }
    }
    return 'file';
  }
  function ctxHide() {
    var e = el('Ctx');
    if (e) { e.style.display = 'none'; e._targets = null; e._upDest = '' }
  }
  function ctxRun(act, targets) {
    var rels = targets.map(function (t) { return t.rel });
    if (act === 'enter') go(rels[0]);
    else if (act === 'rename') renameOne(rels[0]);
    else if (act === 'move') moveRels(rels);
    else if (act === 'reveal') reveal(rels[0]);
    else if (act === 'del') deleteRels(rels);
  }
  function ctxClearSel() {
    S.sel.clear();
    renderFiles();
    updateSel();
  }
  function ctxEl() {
    var e = el('Ctx');
    if (e) return e;
    e = document.createElement('div');
    e.id = P + 'Ctx';
    e.className = 'ctx-menu mlctx';
    e.addEventListener('mousedown', function (ev2) {
      var it = ev2.target.closest ? ev2.target.closest('.ctx-item') : null;
      ev2.preventDefault();
      ev2.stopPropagation();
      if (!it || it.className.indexOf('off') >= 0) return;
      var act = it.getAttribute('data-act');
      var tg = e._targets || [];
      var up = e._upDest || '';
      ctxHide();
      if (act === 'unsel') ctxClearSel();
      else if (act === 'mkdir') mkdirDialog(up);
      else if (act === 'upload') pickUpload(up);
      else if (act === 'uploadfolder') pickUploadFolder(up);
      else if (act === 'refresh') refresh();
      else ctxRun(act, tg);
    });
    document.body.appendChild(e);
    return e;
  }

  // 上传目标：右键命中的是单个文件夹 → 传进该文件夹；否则传进当前所在目录
  function uploadDest(targets) {
    if (targets && targets.length === 1 && targets[0].kind === 'folder') return targets[0].rel;
    return S.cur || '';
  }
  function uploadLabel(dest) {
    if (!dest) return '上传文件到 ' + S.rootName + ' 根目录';
    var parts = String(dest).split('/');
    return '上传文件到 「' + parts[parts.length - 1] + '」';
  }
  function uploadFolderLabel(dest) {
    if (!dest) return '上传文件夹到 ' + S.rootName + ' 根目录';
    var parts = String(dest).split('/');
    return '上传文件夹到 「' + parts[parts.length - 1] + '」';
  }
  function mkdirLabel(dest) {
    if (!dest) return '新建文件夹（' + S.rootName + '）';
    var parts = String(dest).split('/');
    return '新建文件夹（' + parts[parts.length - 1] + '）';
  }
  async function mkdirDialog(destRel) {
    var where = destRel ? ('「' + String(destRel).split('/').pop() + '」') : (S.rootName + ' 根目录');
    var nm = await promptDialog('在 ' + where + ' 下新建文件夹名称：', '', { title: '新建文件夹' });
    if (!nm) return;
    try {
      var j = await postJSON('/api/models/local/mkdir', { rel: destRel || '', name: nm });
      if (!j.ok) { notify(j.msg || '创建失败'); return }
      notify(j.msg || '已创建');
      refresh();
    } catch (e) { notify('创建失败: ' + e.message) }
  }

  function ctxShow(ev, rel, kind) {
    ev.preventDefault();
    ev.stopPropagation();
    // 右键的项目若不在当前勾选中，则把它设为唯一选中项
    if (!S.sel.has(rel)) {
      S.sel.clear();
      S.sel.add(rel);
      renderFiles();
      updateSel();
    }
    var targets = [];
    if (S.sel.size > 1) {
      S.sel.forEach(function (r) { targets.push({ rel: r, kind: kindOf(r) }) });
    } else {
      targets.push({ rel: rel, kind: kind || kindOf(rel) });
    }
    var n = targets.length, one = targets[0];
    var isFolder = (n === 1 && one.kind === 'folder');
    var word = isFolder ? (SCOPE === 'plugins' ? '插件文件夹' : '文件夹') : '文件';
    var upDest = uploadDest(targets);
    var items = [];
    if (isFolder) items.push({ act: 'enter', ico: 'bi-folder2-open', txt: '打开该文件夹' });
    items.push({ act: 'mkdir', ico: 'bi-folder-plus', txt: mkdirLabel(upDest) });
    items.push({ act: 'upload', ico: 'bi-upload', txt: uploadLabel(upDest) });
    items.push({ act: 'uploadfolder', ico: 'bi-folder-symlink', txt: uploadFolderLabel(upDest) });
    items.push({ sep: 1 });
    items.push({ act: 'rename', ico: 'bi-input-cursor-text', txt: '重命名', off: n > 1 });
    items.push({ act: 'move', ico: 'bi-arrows-move', txt: n > 1 ? ('移动 ' + n + ' 项到…') : '移动到…' });
    items.push({
      act: 'reveal', ico: 'bi-crosshair',
      txt: n > 1 ? '在资源管理器中显示第 1 项' : (isFolder ? '在资源管理器中定位' : '在资源管理器中显示')
    });
    items.push({ sep: 1 });
    items.push({ act: 'unsel', ico: 'bi-x-circle', txt: '取消选择' });
    items.push({ act: 'del', ico: 'bi-trash3', txt: n > 1 ? ('删除 ' + n + ' 项（回收站）') : '删除（回收站）', danger: true });
    var head = (n > 1) ? ('已选 ' + n + ' 项') : (word + ' · ' + one.rel);
    ctxPaint(ctxEl(), ev, items, head, one.rel, targets, upDest);
  }

  // 右键菜单统一渲染（行菜单与空白处菜单共用一份）
  function ctxPaint(e, ev, items, head, tip, targets, upDest) {
    e._targets = targets || [];
    e._upDest = upDest || '';
    e.innerHTML = '<div class="mlctx-hd" title="' + mlEsc(tip || '') + '">' + mlEsc(head) + '</div>' +
      items.map(function (it) {
        if (it.sep) return '<div class="ctx-sep"></div>';
        return '<div class="ctx-item' + (it.off ? ' off' : '') + (it.danger ? ' danger' : '') +
          '" data-act="' + it.act + '"><i class="bi ' + it.ico + '"></i>' + mlEsc(it.txt) + '</div>';
      }).join('');
    e.style.display = 'block';
    e.style.left = '0px';
    e.style.top = '0px';
    var w = e.offsetWidth, h = e.offsetHeight;
    e.style.left = Math.max(6, Math.min(ev.clientX, window.innerWidth - w - 8)) + 'px';
    e.style.top = Math.max(6, Math.min(ev.clientY, window.innerHeight - h - 8)) + 'px';
    mlCtxGuardUntil = Date.now() + 400;
  }

  // 空白区右键：当前目录一行都没有时也得能上传（空目录正是"要往里传文件"的场景）
  function ctxShowBlank(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    var dest = S.cur || '';
    ctxPaint(ctxEl(), ev, [
      { act: 'mkdir', ico: 'bi-folder-plus', txt: mkdirLabel(dest) },
      { act: 'upload', ico: 'bi-upload', txt: uploadLabel(dest) },
      { act: 'uploadfolder', ico: 'bi-folder-symlink', txt: uploadFolderLabel(dest) },
      { sep: 1 },
      { act: 'refresh', ico: 'bi-arrow-clockwise', txt: '刷新列表' }
    ], '当前目录 · ' + (dest || S.rootName), dest || S.rootName, [], dest);
  }

  function bindEvents() {
    var tb = el('Tbody');
    if (tb) {
      tb.addEventListener('click', function (e) {
        var cb = e.target.closest ? e.target.closest('input.mlcb') : null;
        if (cb) { setSel(cb.getAttribute('data-rel'), cb.checked); return }
        var tr = e.target.closest ? e.target.closest('tr[data-rel]') : null;
        if (tr && tr.getAttribute('data-kind') === 'folder') go(tr.getAttribute('data-rel'));
      });
      tb.addEventListener('contextmenu', function (e) {
        var tr = e.target.closest ? e.target.closest('tr[data-rel]') : null;
        if (!tr) return;
        ctxShow(e, tr.getAttribute('data-rel'), tr.getAttribute('data-kind') || 'file');
      });
    }
    // 空白区右键（工具条 / 输入框除外）→ 上传到当前目录 / 刷新
    var box = document.getElementById(LO);
    if (box) {
      box.addEventListener('contextmenu', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        if (t.closest('tr[data-rel]')) return;                          // 行上的右键另走 ctxShow
        if (t.closest('input, select, textarea, button, a, label')) return;  // 工具条保留原生菜单
        ctxShowBlank(e);
      });
    }
    document.addEventListener('mousedown', function (e) {
      var m = el('Ctx');
      if (!m || m.style.display === 'none') return;
      if (!e.target.closest || !e.target.closest('#' + P + 'Ctx')) ctxHide();
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') ctxHide() });
    window.addEventListener('resize', ctxHide);
    window.addEventListener('scroll', function () {
      if (Date.now() < mlCtxGuardUntil) return;
      ctxHide();
    }, true);
    window.addEventListener('wheel', ctxHide, { passive: true });
  }
  document.addEventListener('DOMContentLoaded', bindEvents);

  return {
    S: S, setMode: setMode, onEnter: onEnter, go: go, up: up, refresh: refresh,
    sortChanged: sortChanged, filterLocal: filterLocal, searchNow: searchNow,
    openDir: openDir, toggleAll: toggleAll, setSel: setSel, ctxHide: ctxHide,
    clearSel: ctxClearSel, updateSel: updateSel, renderFiles: renderFiles,
    kindOf: kindOf, listItems: listItems,
    pickUpload: pickUpload, pickUploadFolder: pickUploadFolder,
    mkdir: mkdirDialog, ctxShowBlank: ctxShowBlank
  };
}

// 模型管理（ml / ComfyUI/models）与插件管理（lpl / ComfyUI/custom_nodes）
var LBm = mkLocalView({
  prefix: 'ml', scope: 'models', rootName: 'models', store: 'mMode',
  sortKey: 'mlSort', sw: 'mmodeSw', db: 'mmode-db', local: 'mmode-local'
});
var LBp = mkLocalView({
  prefix: 'lpl', scope: 'plugins', rootName: 'custom_nodes', store: 'plLocalMode',
  sw: 'plmodeSw', db: 'plmode-db', local: 'plmode-local'
});
function mlOnEnter() { LBp.ctxHide(); LBm.onEnter() }
function plOnEnter() { LBm.ctxHide(); LBp.onEnter() }

// ---- 下载控制 ----
function renderMiniBar(pct){if(!pct||pct<=0)return '<span style="color:var(--text3);font-size:11px">-</span>';return '<div class="minibar"><div class="minibar-bg"><div class="minibar-fill" style="width:'+pct+'%"></div></div></div><span style="font-size:10px;color:var(--text2);margin-left:4px">'+pct.toFixed(1)+'%</span>'}
function dlBadge(s){var m={active:'bg-on|下载中',paused:'bg-pause|已暂停',waiting:'bg-wait|等待中',complete:'bg-done|已完成',error:'bg-err|错误',removed:'bg-err|已移除',retrying:'bg-stale|重连中',stale:'bg-stale|同步中',queue:'bg-q|排队中'},p=(m[s]||'bg-off|未开始').split('|');return '<span class="badge '+p[0]+'">'+p[1]+'</span>'}
function dlBtns(id,s){var complete=s==='complete';return'<button class="btn btn2 btn-xs" id="bstart-'+id+'" onclick="dlStart('+id+',false)"'+(complete?' disabled':'')+'>'+(complete?'<i class="bi bi-check-lg"></i> 已完成':'<i class="bi bi-download"></i> 下载')+'</button><button class="btn btn4 btn-xs bpause" id="bpause-'+id+'" onclick="dlPause('+id+')" style="'+(s==='active'?'':'display:none')+'">'+'<i class="bi bi-pause-fill"></i> 暂停</button><button class="btn btn4 btn-xs bresume" id="bresume-'+id+'" onclick="dlResume('+id+')" style="'+(s==='paused'?'':'display:none')+'">'+'<i class="bi bi-play-fill"></i> 恢复</button><button class="btn btn3 btn-xs" id="bcancel-'+id+'" onclick="dlCancel('+id+')"'+(s==='active'||s==='paused'||s==='queue'?'':' disabled')+'><i class="bi bi-x-lg"></i> 取消</button>'}
function dlOnSel(id,c){if(c)dlSelIds.add(id);else dlSelIds.delete(id);dlUpCnt()}
function dlSelAll(c){if(c){mData.forEach(function(m){dlSelIds.add(m.id)})}else{dlSelIds.clear()}renderModels()}
function dlUpCnt(){var e=document.getElementById('bcnt');if(e)e.textContent='已选 '+dlSelIds.size}

async function checkA2(){try{var r=await fetch('/api/aria2/status');var d=await r.json();var dot=document.getElementById('a2dot'),t=document.getElementById('a2text');if(d.running){dot.className='a2dot on';t.textContent='aria2c v'+d.version}else{dot.className='a2dot off';t.textContent='aria2c 未运行'}return d.running}catch(e){return false}}
async function a2Start(){try{var r=await fetch('/api/aria2/start',{method:'POST'});var d=await r.json();notify(d.msg);checkA2()}catch(e){notify('启动失败')}}
async function a2Stop(){try{await fetch('/api/aria2/stop',{method:'POST'});checkA2();clearInterval(dlProgTimer);dlProgTimer=null}catch(e){}}
async function a2Restart(){await a2Stop();await new Promise(function(r){setTimeout(r,1500)});await a2Start()}
async function detectA2(){var d=document.getElementById('adr');d.innerHTML='<span style="color:var(--text3);font-size:12px">检测中...</span>';try{var r=await fetch('/api/aria2/detect');var j=await r.json();if(j.found){document.getElementById('sap').value=j.path;d.innerHTML='<span class="db db-ok">'+esc(j.version||j.path)+' ['+j.platform+']</span>'}else{d.innerHTML='<span class="db db-fail">未找到 aria2c</span>'}}catch(e){d.innerHTML='<span class="db db-fail">检测失败</span>'}}
async function batchDl(mir){if(dlSelIds.size===0){notify('请先勾选模型');return}if(!await checkA2()){notify('请先启动 aria2c');return}var ids=Array.from(dlSelIds);try{var r=await fetch('/api/download/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:ids,mirror:mir})});var j=await r.json();if(!j.ok)return;var s=0,q=0;j.results.forEach(function(x){x.ok&&(x.msg.indexOf('队列')>=0?q++:s++)});notify('批量下载: 开始 '+s+' 队列 '+q);dlSelIds.clear();renderModels();dlStartTimer();await restoreDlState()}catch(e){notify('批量下载失败: '+e.message)}}
async function dlStart(id,mir){if(!await checkA2()){notify('请先启动 aria2c');return}try{var r=await fetch('/api/download/start/'+id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mirror:!!mir})});var j=await r.json();if(!j.ok){notify(j.msg);return}if(j.msg.indexOf('队列')>=0){dlQueuedIds.add(id)}else{dlActiveIds.add(id)}dlLastStates[id]={status:j.msg.indexOf('队列')>=0?'queue':'waiting',progress:0};renderModels();dlStartTimer()}catch(e){notify('下载失败: '+e.message)}}
async function dlPause(id){try{await fetch('/api/download/pause/'+id,{method:'POST'});dlLastStates[id]={status:'paused',progress:dlLastStates[id]?dlLastStates[id].progress||0:0};renderModels()}catch(e){}}
async function dlResume(id){try{await fetch('/api/download/resume/'+id,{method:'POST'});dlLastStates[id]={status:'active',progress:dlLastStates[id]?dlLastStates[id].progress||0:0};renderModels()}catch(e){}}
async function dlCancel(id){if(!(await confirmDialog('确认取消？')))return;if(dlQueuedIds.has(id)){try{await fetch('/api/download/cancel_queue/'+id,{method:'POST'});dlQueuedIds.delete(id);delete dlLastStates[id];renderModels()}catch(e){}return}try{var r=await fetch('/api/download/cancel/'+id,{method:'POST'});var j=await r.json();j.ok&&(dlActiveIds.delete(id),delete dlLastStates[id],renderModels())}catch(e){}}

function dlStartTimer(){if(!dlProgTimer)dlProgTimer=setInterval(fetchDlAllStatus,1000)}
async function fetchDlAllStatus(){
  try{var r=await fetch('/api/download/all_status');var d=await r.json(),any=false,nowActive=new Set();
    d.active.forEach(function(x){
      nowActive.add(x.id);any=true;
      var last=dlLastStates[x.id],prevPct=last?last.progress||0:0;
      if((x.stale||x.status==='retrying')&&prevPct>0){
        x.progress=prevPct;
        if(last.completed) x.completed=last.completed;
        if(last.total) x.total=last.total;
        if(last.speed) x.speed=last.speed;
      }
      dlLastStates[x.id]={status:x.status,progress:x.progress||0,total:x.total,completed:x.completed,speed:x.speed,mirror:x.mirror,name:x.name,stale:x.stale||false};
      dlUpdateCell(x.id,x)
    });
    dlPrevActive.forEach(function(id){if(!nowActive.has(id)&&!dlQueuedIds.has(id)){dlActiveIds.delete(id);dlLastStates[id]={status:'complete',progress:100};renderModels()}});
    dlPrevActive=new Set(nowActive);
    d.queue&&d.queue.length>0&&(any=true);
    dlUpdateCards(d.active);dlUpdateQueue(d.queue);
    if(!any){clearInterval(dlProgTimer);dlProgTimer=null}
  }catch(e){}
}
function dlUpdateCell(id,s){var zc=document.getElementById('dsz-'+id),pc=document.getElementById('dpg-'+id),sc=document.getElementById('dst-'+id);if(!zc||!pc||!sc)return;var pct=s.progress||0,cm=s.completed?(s.completed/1048576).toFixed(1):'0',tm=s.total?(s.total/1048576).toFixed(1):'?';zc.textContent=cm+'/'+tm+' MB';pc.innerHTML=renderMiniBar(pct)+(s.stale?' <span style="font-size:10px;color:var(--text3);animation:pulse 1s infinite">⟳</span>':'');sc.innerHTML=dlBadge(s.stale?'stale':s.status);var bs=document.getElementById('bstart-'+id),bp=document.getElementById('bpause-'+id),br=document.getElementById('bresume-'+id),bc=document.getElementById('bcancel-'+id);if(!bs)return;var isStale=s.stale||s.status==='retrying';
if(s.status==='active'||isStale){bs.disabled=true;bs.textContent=isStale?'重连中':'下载中';bp.style.display='';bp.disabled=isStale;br.style.display='none';bc.disabled=false}else if(s.status==='paused'){bs.disabled=true;bp.style.display='none';br.style.display='';br.disabled=false;bc.disabled=false}else if(s.status==='complete'){bs.disabled=true;bs.textContent='已完成';bp.style.display='';bp.disabled=true;br.style.display='none';bc.disabled=true}else if(s.status==='error'||s.status==='removed'){bs.disabled=false;bs.textContent='重试';bp.style.display='';bp.disabled=true;br.style.display='none';bc.disabled=true}}
function dlUpdateCards(active){var con=document.getElementById('plist_dl'),cel=document.getElementById('pcard');if(!con||!cel)return;con.innerHTML='';if(!active||active.length===0){cel.style.display='none'}else{cel.style.display='block';active.forEach(function(s){var pct=s.progress||0,cm=s.completed?(s.completed/1048576).toFixed(1):'0',tm=s.total?(s.total/1048576).toFixed(1):'?',sm=s.speed?(s.speed/1048576).toFixed(2):'0',staleMark=s.stale?'<span style="font-size:10px;color:var(--amber);animation:pulse 1s infinite;margin-left:4px">⟳ 信号弱</span>':'',card=document.createElement('div');card.className='pitem';card.innerHTML='<div class="phd"><span class="pname">'+esc(s.name||'#'+s.id)+(s.mirror?' <span style="color:var(--purple);font-size:11px">[镜像]</span>':'')+'</span><span style="font-size:11px;color:var(--text2)">'+(s.stale?'重连中':s.status)+staleMark+'</span></div><div class="pbar-bg"><div class="pbar-fill" style="width:'+Math.max(pct,3)+'%">'+pct.toFixed(1)+'%</div></div><div class="pinfo"><span><span>'+cm+'</span> / '+tm+' MB</span><span>'+sm+' MB/s</span></div>';con.appendChild(card)})}}
function dlUpdateQueue(queue){var con=document.getElementById('qlist'),cel=document.getElementById('qcard');if(!queue||queue.length===0){cel.style.display='none';return}cel.style.display='block';con.innerHTML='';queue.forEach(function(q){var div=document.createElement('div');div.className='qitem';div.innerHTML='<div class="qpos">'+q.queue_position+'</div><span style="flex:1;font-size:12.5px">'+esc(q.name||'#'+q.id)+'</span><span style="font-size:11px;color:var(--text2)"><code>'+esc(q.save_path)+'</code></span>'+'<button class="btn btn3 btn-xs" onclick="dlCancel('+q.id+')"><i class="bi bi-x-lg"></i> 取消</button>';con.appendChild(div)})}
async function restoreDlState(){
  try{var r=await fetch('/api/download/all_status');var d=await r.json();dlActiveIds.clear();dlQueuedIds.clear();dlLastStates={};dlPrevActive=new Set();
    d.active.forEach(function(x){dlActiveIds.add(x.id);dlPrevActive.add(x.id);dlLastStates[x.id]={status:x.status,progress:x.progress||0,total:x.total,completed:x.completed,speed:x.speed,mirror:x.mirror,name:x.name}});
    d.queue.forEach(function(x){dlQueuedIds.add(x.id);dlLastStates[x.id]={status:'queue',progress:0,name:x.name,mirror:x.mirror}});
    renderModels();dlUpdateCards(d.active);dlUpdateQueue(d.queue);if(d.active.length>0||d.queue.length>0)dlStartTimer()
  }catch(e){}
}
