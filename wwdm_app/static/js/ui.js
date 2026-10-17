// ============================================================
// UI 通用件（v5.0 第 8 步，自 index.html 逐字抽出）
// ------------------------------------------------------------
// esc()            HTML 转义（表格 / 弹窗里所有用户可控字符串都要过这一道）
// notify()         右上角轻提示（替代原生 alert，自动按文案猜 error/success/info）
// confirmDialog()  确认弹窗（替代原生 confirm，支持 danger 样式，返回 Promise<boolean>）
// promptDialog()   输入弹窗（替代原生 prompt，返回 Promise<string|null>）
//
// ⚠️ 必须最先加载：api.js 在 401/403 时会调 notify()，table.js 的状态行要调 esc()。
// ============================================================

function esc(s){var d=document.createElement('div');d.textContent=s||'';return d.innerHTML}

// ============================================================
// 信息弹窗 (替代原生 alert，统一风格)
// ============================================================
var _ntfWrap=null;
function _ntfEnsure(){if(_ntfWrap)return _ntfWrap;_ntfWrap=document.createElement('div');_ntfWrap.id='notify-wrap';document.body.appendChild(_ntfWrap);return _ntfWrap}
function notify(msg,type,dur){
  msg=(msg===undefined||msg===null)?'':String(msg);
  if(!type){
    if(/失败|错误|无效|不存在|未找到|超时|拒绝|异常|不可|不能|请先|请输入|请填写|请至少|请选择|未检测|无效命令|\u274c|\u26a0/.test(msg)){type='error'}
    else if(/成功|已保存|已卸载|已计划|已发送|已创建|已删除|已更新|队列|开始|完成|\u2705/.test(msg)){type='success'}
    else{type='info'}
  }
  var ico='bi-info-circle-fill';
  if(type==='error')ico='bi-x-circle-fill';
  else if(type==='success')ico='bi-check-circle-fill';
  else if(type==='warning')ico='bi-exclamation-triangle-fill';
  var sec=(dur!==undefined&&dur!==null)?dur:(type==='error'?5200:(type==='warning'?4500:3200));
  var wrap=_ntfEnsure();
  var el=document.createElement('div');el.className='ntf ntf-'+type;
  el.innerHTML='<span class="ntf-ico"><i class="bi '+ico+'"></i></span>'
    +'<div class="ntf-body">'+msg.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')+'</div>'
    +'<button class="ntf-x" title="关闭"><i class="bi bi-x-lg"></i></button><span class="ntf-bar"></span>';
  wrap.appendChild(el);
  var bar=el.querySelector('.ntf-bar');bar.style.animationDuration=sec+'ms';
  var timer=null;
  function close(){if(el._closed)return;el._closed=true;el.classList.add('ntf-out');setTimeout(function(){if(el.parentNode)el.parentNode.removeChild(el)},260)}
  function start(){timer=setTimeout(close,sec)}
  el.querySelector('.ntf-x').addEventListener('click',close);
  el.addEventListener('mouseenter',function(){clearTimeout(timer);bar.style.animationPlayState='paused'});
  el.addEventListener('mouseleave',function(){bar.style.animationPlayState='running';start()});
  start();
  return el;
}

// ============================================================
// 确认弹窗 (替代原生 confirm，支持危险/警告样式)
// ============================================================
function confirmDialog(msg,opts){
  opts=opts||{};
  var title=opts.title||'确认操作';
  var okText=opts.okText||'确定';
  var cancelText=opts.cancelText||'取消';
  var danger=!!opts.danger;
  var ico=opts.icon||(danger?'bi-exclamation-triangle-fill':'bi-question-circle-fill');
  return new Promise(function(resolve){
    var mask=document.createElement('div');mask.className='cdlg-mask';
    mask.innerHTML='<div class="cdlg">'
      +'<div class="cdlg-hd"><span class="cdlg-ico '+(danger?'cdlg-ico-danger':'')+'"><i class="bi '+ico+'"></i></span><h3>'+esc(title)+'</h3></div>'
      +'<div class="cdlg-body">'+(msg||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')+'</div>'
      +'<div class="cdlg-ft"><button class="btn btn6 cdlg-cancel">'+esc(cancelText)+'</button>'
      +'<button class="btn '+(danger?'btn3':'btn1')+' cdlg-ok">'+esc(okText)+'</button></div>'
      +'</div>';
    document.body.appendChild(mask);
    function done(v){mask.remove();document.removeEventListener('keydown',onKey);resolve(v)}
    function onKey(e){if(e.key==='Escape'){e.preventDefault();done(false)}else if(e.key==='Enter'){e.preventDefault();done(true)}}
    mask.querySelector('.cdlg-ok').addEventListener('click',function(){done(true)});
    mask.querySelector('.cdlg-cancel').addEventListener('click',function(){done(false)});
    mask.addEventListener('click',function(e){if(e.target===mask)done(false)});
    document.addEventListener('keydown',onKey);
    var okBtn=mask.querySelector('.cdlg-ok');if(okBtn)okBtn.focus();
  });
}

// ============================================================
// 输入弹窗 (替代原生 prompt)
// ============================================================
function promptDialog(msg,def,opts){
  opts=opts||{};
  var title=opts.title||'请输入';
  var okText=opts.okText||'确定';
  var cancelText=opts.cancelText||'取消';
  var placeholder=opts.placeholder||'';
  return new Promise(function(resolve){
    var mask=document.createElement('div');mask.className='cdlg-mask';
    mask.innerHTML='<div class="cdlg">'
      +'<div class="cdlg-hd"><span class="cdlg-ico"><i class="bi bi-pencil-square"></i></span><h3>'+esc(title)+'</h3></div>'
      +'<div class="cdlg-body">'+(msg||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')+'</div>'
      +'<div class="cdlg-input-wrap"><input class="cdlg-input" type="text" placeholder="'+esc(placeholder)+'" value="'+esc(def||'')+'"></div>'
      +'<div class="cdlg-ft"><button class="btn btn6 cdlg-cancel">'+esc(cancelText)+'</button>'
      +'<button class="btn btn1 cdlg-ok">'+esc(okText)+'</button></div>'
      +'</div>';
    document.body.appendChild(mask);
    var input=mask.querySelector('.cdlg-input');
    function done(v){mask.remove();document.removeEventListener('keydown',onKey);resolve(v)}
    function onKey(e){if(e.key==='Escape'){e.preventDefault();done(null)}else if(e.key==='Enter'){e.preventDefault();done(input.value)}}
    mask.querySelector('.cdlg-ok').addEventListener('click',function(){done(input.value)});
    mask.querySelector('.cdlg-cancel').addEventListener('click',function(){done(null)});
    mask.addEventListener('click',function(e){if(e.target===mask)done(null)});
    document.addEventListener('keydown',onKey);
    input.focus();input.select();
  });
}
