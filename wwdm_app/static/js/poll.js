// ============================================================
// 轮询管理器（v5.0 第 8 步）
// ------------------------------------------------------------
// 之前页面里有 6 处各写各的 setInterval，节奏五花八门（800ms / 1.2s / 2s / 30s），
// 每处都要自己判断 document.hidden、自己 try/catch、自己记 timer 句柄防重复建。
// 统一收进这里：
//
//   Poll.register('download', fetchDlAllStatus, 2000)   // 注册并开跑（幂等）
//   Poll.stop('download')                               // 离开页面时停
//   Poll.stopAll()                                      // 全停
//   Poll.once('download')                               // 立刻补一次，不等下个周期
//
// 三条硬约束（照抄到其它公共件）：
//   ① 同名 register 只改配置、不新开定时器 —— 以前 `if(!timer)timer=setInterval(...)`
//      这种写法一旦漏判就会两条线程一起轮询。
//   ② 回调抛异常 / Promise reject 都只打印，不连带把定时器打死。
//   ③ 页面在后台时自动跳过；切回来立刻补一次 —— 后台标签页不该继续打接口，
//      但用户切回来时也不该看到上上个周期的旧数据。
// ============================================================
(function () {
  var _items = {};        // name -> {fn, ms, id}
  var _boundVis = false;

  function _safe(name, fn) {
    try {
      var r = fn();
      if (r && typeof r.then === 'function') {
        r.then(null, function (e) {
          try { console.error('[poll] ' + name + ' 异步失败', e); } catch (_) { }
        });
      }
    } catch (e) {
      try { console.error('[poll] ' + name + ' 抛异常', e); } catch (_) { }
    }
  }

  function _tick(name) {
    var it = _items[name];
    if (!it || !it.id) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    _safe(name, it.fn);
  }

  var Poll = {
    /** 注册（或改写）一个轮询。同名重复调用只换回调与间隔，不会叠加定时器。 */
    register: function (name, fn, ms, opts) {
      opts = opts || {};
      var it = _items[name];
      if (it) {
        it.fn = fn; it.ms = ms;
        if (it.id) { clearInterval(it.id); it.id = setInterval(function () { _tick(name); }, ms); }
        return it;
      }
      it = _items[name] = { fn: fn, ms: ms, id: 0 };
      it.id = setInterval(function () { _tick(name); }, ms);
      if (opts.immediate !== false) _safe(name, fn);   // 首帧先跑一次，别让用户空等一个周期
      _bindVis();
      return it;
    },

    /** 停掉一个（切换页面时用，避免离开后还在打接口）。 */
    stop: function (name) {
      var it = _items[name];
      if (!it) return false;
      if (it.id) { clearInterval(it.id); it.id = 0; }
      return true;
    },

    /** 全停。 */
    stopAll: function () {
      for (var k in _items) { if (_items.hasOwnProperty(k)) this.stop(k); }
    },

    /**
     * 立刻跑一次（不等下个周期）。
     * ⚠️ 只作用于「在跑」的轮询 —— 与 onceAll() 口径一致。stop() 之后再 once()
     * 就是自相矛盾：想手动刷一次就直接调那个函数，别借道 Poll。
     */
    once: function (name) {
      var it = _items[name];
      if (it && it.id) _safe(name, it.fn);
    },

    /** 全部立刻跑一次（页面切回来、或手动刷新时）。 */
    onceAll: function () {
      for (var k in _items) {
        if (_items.hasOwnProperty(k) && _items[k].id) _safe(k, _items[k].fn);
      }
    },

    /** 改名 / 改节奏；未注册过则等同于 register。 */
    setInterval: function (name, ms) {
      var it = _items[name];
      if (!it) return this.register(name, function () { }, ms);
      it.ms = ms;
      if (it.id) { clearInterval(it.id); it.id = setInterval(function () { _tick(name); }, ms); }
      return it;
    },

    /** 调试用：看现在谁在跑。 */
    _items: function () { return _items; }
  };

  function _bindVis() {
    if (_boundVis || typeof document === 'undefined') return;
    _boundVis = true;
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) Poll.onceAll();
    });
  }

  window.Poll = Poll;
})();
