
// ============================================================
// INIT
// ============================================================
loadPaths();loadSF();
var lastPage=sessionStorage.getItem('lastPage');
// 'notes' 自 v5.1 起不再是页面（改为 FAB 唤出的弹窗）。旧 sessionStorage 里可能
// 留着它，直接 switchPage 会因没有 page-notes 容器而导致整个主区空白。
if(lastPage&&lastPage!=='launcher'&&lastPage!=='notes'){switchPage(null,lastPage)}
else{cuiCheckStatus()}
// ====== 右下角扇形功能菜单 ======
(function(){
  var fabWrap=document.getElementById('fabWrap'),fabMain=document.getElementById('fabMain');
  var fabFan=document.getElementById('fabFan'),mainEl=document.getElementById('main');
  if(!fabWrap||!fabMain||!fabFan)return;
  var open=false;

  // 展开/收起
  function toggleFab(force){
    open=(typeof force==='boolean')?force:!open;
    fabFan.classList.toggle('open',open);
    fabMain.classList.toggle('active',open);
  }

  // ---- 拖动放置（默认右下角，可拖到任意位置，位置持久化）----
  var dragging=false,moved=false,sx=0,sy=0,startML=0,startMT=0,offX=0,offY=0;
  function fabClamp(v,min,max){return Math.max(min,Math.min(max,v))}
  // 根据位置决定扇形展开方向（靠近顶部则向下展开）
  function fabUpdateDir(){
    var mr=fabMain.getBoundingClientRect();
    var cy=mr.top+mr.height/2;
    if(cy<window.innerHeight/2){fabWrap.classList.add('fab-down')}
    else{fabWrap.classList.remove('fab-down')}
  }
  function fabSavePos(){
    try{localStorage.setItem('wwdm_fab_pos',JSON.stringify({left:fabWrap.style.left,top:fabWrap.style.top,down:fabWrap.classList.contains('fab-down')}))}catch(e){}
  }
  function fabPlaceDefault(){
    var mr=fabMain.getBoundingClientRect(),wr=fabWrap.getBoundingClientRect();
    var ox=mr.left-wr.left,oy=mr.top-wr.top;
    var mL=window.innerWidth-28-48,mT=window.innerHeight-28-48;
    fabWrap.style.left=(mL-ox)+'px';
    fabWrap.style.top=(mT-oy)+'px';
    fabWrap.style.right='auto';fabWrap.style.bottom='auto';
    fabWrap.classList.remove('fab-down');
  }
  function fabApplyPos(pos){
    if(!pos)return false;
    var l=parseFloat(pos.left),t=parseFloat(pos.top);
    if(isNaN(l)||isNaN(t))return false;
    var mr=fabMain.getBoundingClientRect(),wr=fabWrap.getBoundingClientRect();
    var ox=mr.left-wr.left,oy=mr.top-wr.top;
    var mL=fabClamp(l+ox,0,window.innerWidth-48);
    var mT=fabClamp(t+oy,0,window.innerHeight-48);
    fabWrap.style.left=(mL-ox)+'px';
    fabWrap.style.top=(mT-oy)+'px';
    fabWrap.style.right='auto';fabWrap.style.bottom='auto';
    if(pos.down)fabWrap.classList.add('fab-down');
    else fabWrap.classList.remove('fab-down');
    return true;
  }
  // 恢复上次位置，否则默认右下角
  (function(){
    var saved=null;
    try{saved=JSON.parse(localStorage.getItem('wwdm_fab_pos')||'null')}catch(e){}
    if(!saved||!fabApplyPos(saved))fabPlaceDefault();
    fabUpdateDir();
  })();

  fabMain.addEventListener('pointerdown',function(e){
    if(e.button!==undefined&&e.button!==0)return;
    dragging=true;moved=false;
    sx=e.clientX;sy=e.clientY;
    var mr=fabMain.getBoundingClientRect(),wr=fabWrap.getBoundingClientRect();
    offX=mr.left-wr.left;offY=mr.top-wr.top;
    startML=mr.left;startMT=mr.top;
    fabMain.classList.add('dragging');
    try{fabMain.setPointerCapture(e.pointerId)}catch(_){}
    e.stopPropagation();
  });
  fabMain.addEventListener('pointermove',function(e){
    if(!dragging)return;
    var dx=e.clientX-sx,dy=e.clientY-sy;
    if(!moved&&(Math.abs(dx)>4||Math.abs(dy)>4)){moved=true;}
    if(!moved)return;
    var mL=fabClamp(startML+dx,0,window.innerWidth-48);
    var mT=fabClamp(startMT+dy,0,window.innerHeight-48);
    fabWrap.style.left=(mL-offX)+'px';
    fabWrap.style.top=(mT-offY)+'px';
    fabUpdateDir();
  });
  function fabEndDrag(e){
    if(!dragging)return;
    dragging=false;
    fabMain.classList.remove('dragging');
    try{fabMain.releasePointerCapture(e.pointerId)}catch(_){}
    if(moved){fabSavePos();}
    else{toggleFab();}
  }
  fabMain.addEventListener('pointerup',fabEndDrag);
  fabMain.addEventListener('pointercancel',fabEndDrag);

  // 窗口尺寸变化时，重新约束位置不越界
  window.addEventListener('resize',function(){
    var mr=fabMain.getBoundingClientRect(),wr=fabWrap.getBoundingClientRect();
    var ox=mr.left-wr.left,oy=mr.top-wr.top;
    var mL=fabClamp(mr.left,0,window.innerWidth-48);
    var mT=fabClamp(mr.top,0,window.innerHeight-48);
    fabWrap.style.left=(mL-ox)+'px';
    fabWrap.style.top=(mT-oy)+'px';
    fabUpdateDir();
  });

  // 点击空白处收起
  document.addEventListener('click',function(e){
    if(!fabWrap.contains(e.target)&&open)toggleFab(false);
  });

  // 1. 回到顶部
  document.getElementById('fabBtnTop').addEventListener('click',function(){
    mainEl.scrollTo({top:0,behavior:'smooth'});toggleFab(false);
  });

  // 3. 主题切换（日间/夜间）
  var themeBtn=document.getElementById('fabBtnTheme');
  // v6.1：明暗以**服务端 ui_theme 为准**（base.html 直出），localStorage 仅作缓存；
  // 网页里点切换时同步 POST 回服务端，悬浮球/托盘才读得到同一个值。
  var _themeLocalAt=0;
  function applyTheme(t,persist){
    document.documentElement.setAttribute('data-theme',t);
    document.documentElement.setAttribute('data-ui-theme',t);
    try{localStorage.setItem('wwdm_theme',t)}catch(e){}
    themeBtn.title = t==='dark'?'切换到日间':'切换到夜间';
    var sun=themeBtn.querySelector('.ico-sun'),moon=themeBtn.querySelector('.ico-moon');
    if(sun)sun.style.display=t==='dark'?'none':'';
    if(moon)moon.style.display=t==='dark'?'':'none';
    if(persist){
      _themeLocalAt=Date.now();
      try{
        fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({ui_theme:t})}).catch(function(){});
      }catch(e){}
    }
  }
  function currentTheme(){
    // 服务端直出的 data-ui-theme 优先；缺失才回退本地缓存
    var h=document.documentElement.getAttribute('data-ui-theme');
    if(h==='dark'||h==='light')return h;
    try{var t=localStorage.getItem('wwdm_theme'); if(t)return t}catch(e){}
    return 'light';
  }
  themeBtn.addEventListener('click',function(){
    applyTheme(currentTheme()==='dark'?'light':'dark',true);   // 同步回服务端
    toggleFab(false);
  });
  applyTheme(currentTheme(),false);

  // v6.3：SSE 即时推送（服务端一变就推，延迟 <0.3s）；
  // EventSource 不可用/断开时，低频轮询兜底。
  function _applyRemoteTheme(s){
    if(Date.now()-_themeLocalAt<5000)return;          // 刚本地切换，等服务端落盘
    if(s!=='dark'&&s!=='light')return;
    var cur=document.documentElement.getAttribute('data-theme')||currentTheme();
    if(s!==cur)applyTheme(s,false);
  }
  var _wpSig='';
  function _applyRemoteState(d){
    if(!d)return;
    _applyRemoteTheme(d.ui_theme);
    try{
      if(typeof applyPreset==='function' && d.theme_preset){
        if(document.documentElement.getAttribute('data-preset')!==d.theme_preset)
          applyPreset(d.theme_preset);
      }
      if(typeof applyWallpaper==='function' && d.wallpaper_path!==undefined){
        var sig=(d.wallpaper_path||'')+'|'+(d.wallpaper_blur||0);
        if(sig!==_wpSig){_wpSig=sig;applyWallpaper(d.wallpaper_path,d.wallpaper_blur);}
      }
    }catch(e){}
  }
  try{
    if(window.EventSource){
      var _themeES=new EventSource('/api/theme/stream');
      _themeES.onmessage=function(ev){
        try{var d=JSON.parse(ev.data||'{}');_applyRemoteState(d)}catch(e){}
      };
      // onerror 时浏览器会自动重连；下面的轮询继续兜底
    }
  }catch(e){}

  function pollTheme(){
    if(Date.now()-_themeLocalAt<5000)return;
    fetch('/api/settings',{cache:'no-store'}).then(function(r){return r.json()})
      .then(function(d){_applyRemoteState(d);}).catch(function(){});
  }
  setInterval(pollTheme,5000);

  // 4. 定时关机（延迟取自全局设置 sdly；0 = 立即关机）
  function fabDelay(){
    var el=document.getElementById('sdly');
    var raw=(el&&el.value!=null)?String(el.value).trim():'';
    if(raw==='')return 60;                 // 设置页没读到 → 用默认 60 秒
    var n=Number(raw);
    // 必须严格整数：parseInt('0.5') 会得到 0，被当成"立即关机"就危险了
    if(!isFinite(n)||Math.round(n)!==n)return null;
    return n;                              // 0 与负数原样返回，交给下面校验
  }
  function fabPlan(){
    var delay=fabDelay();
    if(delay===null){notify('关机延迟请填 0-3600 的整数秒（0 = 立即关机）');return}
    if(delay<0){notify('关机延迟不能为负数，请到「全局设置」修改');return}
    if(delay>3600){notify('关机延迟最多 3600 秒，请到「全局设置」修改');return}
    var body=delay<=0
      ? '确定立即关机吗？\n将立刻执行关机，没有取消窗口，请先保存好所有工作！'
      : '确定 '+delay+' 秒后关机吗？\n倒计时期间可再次点击关机按钮取消。';
    confirmDialog(body,{danger:true,title:delay<=0?'立即关机':'定时关机',okText:delay<=0?'立即关机':'确定'}).then(function(ok){
      if(!ok)return;
      fetch('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delay:delay})})
      .then(function(r){return r.json()}).then(function(j){
        if(!j.ok){notify('关机指令失败: '+(j.msg||'未知原因'));return}
        notify(delay<=0?'已发出立即关机指令，无法取消。':('已计划 '+delay+' 秒后关机，再次点击关机按钮可取消。'))
      }).catch(function(err){notify('关机指令失败: '+err.message)});
    });
  }
  document.getElementById('fabBtnPower').addEventListener('click',function(){
    toggleFab(false);
    // 先查询是否已有计划中的关机任务，有则提示取消
    fetch('/api/shutdown/status').then(function(r){return r.json()}).then(function(st){
      if(st&&st.scheduled){
        confirmDialog('已有计划中的关机任务（约 '+st.remaining+' 秒后执行），要取消吗？',{title:'定时关机',danger:false,okText:'取消关机',cancelText:'保留'}).then(function(ok){
          if(!ok)return;
          fetch('/api/shutdown/cancel',{method:'POST'}).then(function(r){return r.json()}).then(function(j){
            notify(j.ok?('已取消关机'):(j.msg||'取消失败'))
          }).catch(function(err){notify('取消失败: '+err.message)});
        });
        return;
      }
      fabPlan();
    }).catch(fabPlan);   // 状态查询失败时降级为直接计划关机
  });
})();
