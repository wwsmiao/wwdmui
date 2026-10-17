// ============================================================
// 表格渲染助手（v5.0 第 8 步）
// ------------------------------------------------------------
// 页面里「表格状态行」那段 HTML 抄了 6 遍（加载中 / 暂无数据 / 加载失败），
// 每处手写 colspan、内联 style、配色，改一处要改六处。这里收成两个函数：
//
//   tblState(tbody, 9, 'error', '加载失败')     // 写状态行并返回 true
//   tblRows(tbody, rows, function(r){ return '<tr>...' })
//
// 刻意做得**很薄**：只负责「往 tbody 里写 HTML」这一件事，不接管分页、
// 不接管选择状态 —— 那些逻辑各页差异大，抽象出来只会变成四处漏水的接口。
// ============================================================

/** 状态行文案与配色的唯一出处。kind: loading | empty | error */
function tblState(tbody, colspan, kind, msg) {
  var conf = {
    loading: { color: 'var(--text3)', text: msg || '加载中…' },
    empty: { color: 'var(--text3)', text: msg || '暂无数据' },
    error: { color: 'var(--red)', text: msg || '加载失败' }
  }[kind] || { color: 'var(--text3)', text: msg || '' };
  if (typeof tbody === 'string') tbody = document.getElementById(tbody);
  if (!tbody) return false;
  tbody.innerHTML = '<tr><td colspan="' + (colspan || 1) +
    '" style="text-align:center;color:' + conf.color + ';padding:24px">' +
    esc(conf.text) + '</td></tr>';
  return true;
}

/**
 * 渲染数据行。build 返回每行的 <tr> 字符串；rows 为空时自动写「暂无数据」行。
 * @param {string|Element} tbody
 * @param {Array} rows
 * @param {function} build (row, index) -> string
 * @param {object} opts { colspan: 空/状态行的列数, emptyMsg: 空态文案 }
 */
function tblRows(tbody, rows, build, opts) {
  opts = opts || {};
  if (typeof tbody === 'string') tbody = document.getElementById(tbody);
  if (!tbody) return 0;
  rows = rows || [];
  if (!rows.length) {
    tblState(tbody, opts.colspan || 1, 'empty', opts.emptyMsg);
    return 0;
  }
  var h = [];
  for (var i = 0; i < rows.length; i++) h.push(build(rows[i], i));
  tbody.innerHTML = h.join('');
  return rows.length;
}

/**
 * 追加行（滚动加载 / 追加分页用），不清空已有内容。
 */
function tblAppend(tbody, rows, build) {
  if (typeof tbody === 'string') tbody = document.getElementById(tbody);
  if (!tbody || !rows || !rows.length) return 0;
  var h = [];
  for (var i = 0; i < rows.length; i++) h.push(build(rows[i], i));
  tbody.insertAdjacentHTML('beforeend', h.join(''));
  return rows.length;
}
