// ============================================================
// 统一请求层（v4.1 / S2 起；v5.0 第 8 步自 index.html 逐字抽出）
// ------------------------------------------------------------
// 分两个东西，职责不重叠：
//
//   1) api(url, opts) —— 显式入口：body 直接传对象、自动补 JSON 头、
//      非 2xx 抛带 status / traceId 的 Error。
//
//   2) 下面的 fetch 兜底 —— 页面里已有 118 处 fetch，形态几十种组合，
//      逐个改写改动面太大、极易引入回归，所以**不去动它们**。兜底层只做三件
//      **不改变返回值**的事：
//        a. 显式声明 credentials:'same-origin'
//        b. 401 / 403 各自统一提示一次 —— 之前这类错误被各处的 catch 吞掉，
//           用户只看到「点了没反应」。401 = 访问令牌失效（错误体带 need_token），
//           403 = 同源校验拒绝（多为换机 / 换端口后从别处打开）
//        c. 其它 4xx / 5xx 把 status + trace_id 打进 console，配后端日志排障
//
//      返回值仍是原始 Response，所以 .json() / .blob() 等既有写法一律不受影响；
//      业务错误（HTTP 200 + ok:false）也不在这里处理，仍由调用方 notify。
// ============================================================

// ============================================================
// 统一请求层（v4.1 / S2）
// ------------------------------------------------------------
// 分两个东西，职责不重叠：
//
//   1) api(url, opts) —— 显式入口，给新代码 / 后续重构用：body 直接传对象、
//      自动补 JSON 头、非 2xx 抛带 status / traceId 的 Error。
//
//   2) 下面的 fetch 兜底 —— 页面里已经有 120+ 处 fetch，形态有 30 多种组合，
//      逐个改写改动面太大、极易引入回归，所以**不去动它们**。兜底层只做三件
//      **不改变返回值**的事：
//        a. 显式声明 credentials:'same-origin'
//        b. 401 / 403 各自统一提示一次 —— 之前这类错误被各处的 catch 吞掉，
//           用户只看到「点了没反应」。401 = 访问令牌失效（错误体带 need_token），
//           403 = 同源校验拒绝（多为换机 / 换端口后从别处打开）
//        c. 其它 4xx / 5xx 把 status + trace_id 打进 console，配后端日志排障
//
//      返回值仍是原始 Response，所以 .json() / .blob() 等既有写法一律不受影响；
//      业务错误（HTTP 200 + ok:false）也不在这里处理，仍由调用方 notify。
// ============================================================
var _nativeFetch = window.fetch.bind(window);
var _apiHintShown = {};

function _apiFirstHint(key, msg, type) {
  if (_apiHintShown[key]) return;
  _apiHintShown[key] = 1;
  try { notify(msg, type || 'error'); } catch (e) { }
}

function _apiExplain(status, data) {
  if (status === 401) return (data && data.msg) || '访问令牌无效或已过期';
  if (status === 403) return (data && data.msg) || '请求被同源校验拒绝';
  return 'HTTP ' + status;
}

window.fetch = function (input, init) {
  init = init || {};
  if (init.credentials === undefined) init.credentials = 'same-origin';
  return _nativeFetch(input, init).then(function (res) {
    if (res.status >= 400) {
      // 错误体只能读一次：clone 一份来看，原 Response 照常交给调用方。
      // 整段再包一层 try —— clone() 在极端情况下会抛（body 已被消费），
      // 兜底层绝不能反过来把调用方那一行 await fetch 打断。
      try {
        res.clone().json().then(function (d) {
          var url = (typeof input === 'string') ? input : ((input && input.url) || '');
          var msg = _apiExplain(res.status, d);
          if (res.status === 401) {
            _apiFirstHint('401', '访问令牌已失效：' + msg + '（刷新页面即可重新授权）');
          } else if (res.status === 403) {
            _apiFirstHint('403', '请求被拒绝：' + msg + '（请从本机地址或带令牌的地址打开）');
          }
          try {
            console.error('[api] ' + res.status + ' ' + url + ' trace=' + ((d && d.trace_id) || '-') + ' ' + msg);
          } catch (e) { }
        }).catch(function () { });
      } catch (e) { }
    }
    return res;
  });
};

async function api(url, opts) {
  opts = opts || {};
  var init = {
    method: opts.method || (opts.body === undefined ? 'GET' : 'POST'),
    credentials: 'same-origin',
    headers: Object.assign({}, opts.headers || {})
  };
  if (opts.body !== undefined && opts.body !== null) {
    if (typeof opts.body === 'string') {
      init.body = opts.body;
    } else {
      init.body = JSON.stringify(opts.body);
      if (!init.headers['Content-Type']) init.headers['Content-Type'] = 'application/json';
    }
  }
  // 走上面那层包装（而不是 _nativeFetch）：401 / 403 的统一提示和 trace
  // 日志由它负责，api() 只额外把「非 2xx」升级成可 catch 的 Error。
  var res = await window.fetch(url, init);
  var data = null;
  try { data = await res.json(); } catch (e) { }
  if (!res.ok) {
    var err = new Error(_apiExplain(res.status, data));
    err.status = res.status;
    err.traceId = data && data.trace_id;
    err.data = data;
    throw err;
  }
  return data;
}
