

// ============================================================
// ============================================================
// 抖音提取
// ============================================================
var dyResults=[],dyTabIdx=0,
  dyImages=[],dySelected={},dyVideoUrl=null,dyCoverUrl=null,dyAwemeId=null,dyType='';

function dyTabLabel(r,i){
  var title=r.desc||'';
  if(title)title=title.slice(0,10);
  if(!title){title=(r.target_url||'').split('/').pop().slice(0,12)||'#'+(i+1)}
  var icon=r.type==='video'?'🎥':(r.type==='note'?'🖼':'❓');
  if(r.type==='error')icon='❌';
  return icon+' '+title;
}

function dySwitchTab(i){
  if(i<0||i>=dyResults.length)return;
  dyTabIdx=i;
  var bars=document.querySelectorAll('#dyTabBar .dy-tab');
  bars.forEach(function(b,j){b.classList.toggle('dy-tab-on',j===i)});
  var r=dyResults[i];
  dyType=r.type;dyVideoUrl=r.video_url||null;dyCoverUrl=r.cover_url||null;dyAwemeId=r.aweme_id||null;
  dySelected={};
  dyImages=(r.image_urls||[]).map(function(u,j){return{url:u,idx:j}});
  document.getElementById('dyResultDesc').innerText=(r.desc||'')+'\n链接: '+(r.target_url||'')+'\n共 '+(r.image_count||0)+' 张图片';
  document.getElementById('dyVideoSection').style.display='none';
  document.getElementById('dyGridWrap').style.display='none';
  if(r.type==='video'){
    document.getElementById('dyVideoSection').style.display='';
    if(r.video_url){
      var vid=document.getElementById('dyCoverVideo');
      vid.src=r.video_url;vid.style.display='';
      document.getElementById('dyCoverOnly').style.display='none';
      dyMsg('✅ 视频已就绪','ok');
    }else if(r.cover_url){
      document.getElementById('dyCoverVideo').style.display='none';
      document.getElementById('dyCoverOnly').style.display='';
      document.getElementById('dyCoverImg').src=r.cover_url;
      dyMsg('🖼 封面已加载 (视频需Cookie)','');
    }
  }else if(r.type==='note'){
    dyRenderGrid();
    document.getElementById('dyGridWrap').style.display='';
    dyMsg('共 '+dyImages.length+' 张图片','');
  }else if(r.type==='error'){
    dyMsg(r.msg||'请求失败','err');
  }else{
    dyMsg('无法识别内容类型','');
  }
  var dl=document.getElementById('dyDlCard');
  if(dl)dl.style.display=(dyType==='video'||dyType==='note')?'':'none';
  var tr=document.getElementById('dyTmplRow');
  if(tr)tr.style.display=(dyType==='note')?'':'none';
  dyUpdateDlBtn();
}

function loadDySettings(){
  fetch('/api/settings').then(function(r){return r.json()}).then(function(s){
    setVal('dySaveDir',s.douyin_save_dir);
    setVal('dyNameTmpl',s.douyin_name_template||'{index:02d}.{ext}');
  }).catch(function(){});
}

async function dyFetch(){
  var text=document.getElementById('dyUrl').value.trim();
  if(!text){dyMsg('请输入抖音链接','err');return}
  var btn=document.getElementById('dyFetchBtn');
  btn.disabled=true;btn.innerHTML='<i class="bi bi-search"></i> 提取中...';
  dyMsg('正在提取链接...','');
  dyResults=[];dyTabIdx=0;
  dyImages=[];dySelected={};dyVideoUrl=null;dyCoverUrl=null;dyAwemeId=null;dyType='';
  document.getElementById('dyResultCard').style.display='none';
  document.getElementById('dyVideoSection').style.display='none';
  document.getElementById('dyGridWrap').style.display='none';
  var dl=document.getElementById('dyDlCard');if(dl)dl.style.display='none';
  document.getElementById('dyDlProgress').style.display='none';
  document.getElementById('dyTabBar').innerHTML='';
  try{
    var r=await fetch('/api/douyin/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text})});
    var j=await r.json();
    if(!j.ok){dyMsg(j.msg||'提取失败','err');btn.disabled=false;btn.innerHTML='<i class="bi bi-search"></i> 提取';return}
    dyResults=j.results||[];
    if(!dyResults.length){dyMsg('无结果','err');btn.disabled=false;btn.innerHTML='<i class="bi bi-search"></i> 提取';return}
    var bar=document.getElementById('dyTabBar');
    dyResults.forEach(function(res,idx){
      var tab=document.createElement('span');
      tab.className='dy-tab'+(idx===0?' dy-tab-on':'');
      tab.textContent=dyTabLabel(res,idx);
      tab.style.cssText='display:inline-block;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer;background:var(--bg3);color:var(--text2);white-space:nowrap;transition:all .15s;user-select:none';
      tab.addEventListener('click',function(){dySwitchTab(idx)});
      bar.appendChild(tab);
    });
    var style=document.getElementById('dyTabStyle');
    if(!style){
      style=document.createElement('style');style.id='dyTabStyle';
      style.textContent='.dy-tab-on{background:var(--accent1)!important;color:#fff!important}';
      document.head.appendChild(style);
    }
    document.getElementById('dyResultCard').style.display='';
    dySwitchTab(0);
    dyMsg('✅ 已提取 '+dyResults.length+' 个链接','ok');
  }catch(e){dyMsg('请求失败: '+e.message,'err')}
  btn.disabled=false;btn.innerHTML='<i class="bi bi-search"></i> 提取';
}

function dyRenderGrid(){
  var grid=document.getElementById('dyGrid');
  grid.innerHTML='';
  dyImages.forEach(function(img,i){
    var card=document.createElement('div');
    card.setAttribute('data-i',i);card.className='dy-img-card';
    card.style.position='relative';card.style.borderRadius='6px';card.style.overflow='hidden';card.style.cursor='pointer';
    var checked=!!dySelected[i];
    var cbBg=checked?'var(--accent1)':'rgba(0,0,0,0.45)';
    var cbBorder=checked?'var(--accent1)':'rgba(255,255,255,0.35)';
    card.innerHTML='<img src="'+img.url+'" loading="lazy" style="width:100%;height:140px;object-fit:cover;display:block" crossorigin="anonymous" onerror="this.style.background=&apos;var(--bg3)&apos;;this.style.minHeight=&apos;80px&apos;">'
      +'<div class="dy-chk" style="position:absolute;top:5px;left:5px;width:22px;height:22px;border-radius:4px;background:'+cbBg+';border:2px solid '+cbBorder+';display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;transition:all .15s">'+(checked?'\u2714':'')+'</div>'
      +'<div style="position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(58,62,70,0.55));color:#fff;font-size:10px;padding:14px 4px 3px">#'+(i+1)+'</div>';
    card.addEventListener('click',function(){
      dyToggleSelect(i);dyRenderGrid();
    });
    if(checked)card.style.boxShadow='0 0 0 2px var(--accent1)';
    grid.appendChild(card);
  });
}

function dyToggleSelect(i){
  if(dySelected[i]){delete dySelected[i]}else{dySelected[i]=true}
  dyUpdateDlBtn();
}

function dySelectAll(){
  dyImages.forEach(function(_,i){dySelected[i]=true});
  dyRenderGrid();dyUpdateDlBtn();
}

function dyDeselectAll(){
  dySelected={};dyRenderGrid();dyUpdateDlBtn();
}

function dyUpdateDlBtn(){
  var btn=document.getElementById('dyDlBtn');
  var dBtn=document.getElementById('dyDlDeselectBtn');
  if(dyType==='video'){
    btn.textContent='\ud83d\udce5 下载视频';
    btn.disabled=!dyVideoUrl;
    if(dBtn)dBtn.style.display='none';
  }else{
    var n=Object.keys(dySelected).length;
    btn.textContent='\ud83d\udce5 下载选中 ('+n+')';
    btn.disabled=(n===0);
    if(dBtn)dBtn.style.display=(n>0)?'':'none';
  }
}

async function dyDownload(){
  var saveDir=document.getElementById('dySaveDir').value.trim();
  if(!saveDir){dyDlMsg('请填写保存目录','err');return}
  var btn=document.getElementById('dyDlBtn');
  btn.disabled=true;btn.textContent='添加中...';
  dyDlMsg('','');
  try{
    var resp;
    if(dyType==='video'){
      resp=await fetch('/api/douyin/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:dyVideoUrl,aweme_id:dyAwemeId,save_dir:saveDir})});
    }else{
      var urls=dyImages.filter(function(_,i){return dySelected[i]}).map(function(x){return x.url});
      var tmpl=document.getElementById('dyNameTmpl').value.trim()||'{index:02d}.{ext}';
      resp=await fetch('/api/douyin/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls:urls,save_dir:saveDir,name_template:tmpl})});
    }
    var j=await resp.json();
    if(j.ok){
      if(dyType==='video' && j.gid){
        await dyMonitorVideoProgress(j.gid);
      }else if(j.gid){
        dyDlMsg('\u2705 视频已添加到aria2下载队列 (gid='+j.gid+')','ok');
      }else if(j.gids){
        dyDlMsg('\u2705 已添加 '+j.added+'/'+j.total+' 张图片到aria2下载队列','ok');
      }else{
        dyDlMsg('\u2705 下载任务已添加','ok');
      }
      dyDeselectAll();
    }else{
      dyDlMsg('失败: '+(j.msg||''),'err');
    }
  }catch(e){dyDlMsg('失败: '+e.message,'err')}
  btn.disabled=false;dyUpdateDlBtn();
}

async function dyMonitorVideoProgress(gid){
  var bar=document.getElementById('dyDlProgress');
  var fill=document.getElementById('dyDlBar');
  var sizeEl=document.getElementById('dyDlSize');
  var speedEl=document.getElementById('dyDlSpeed');
  var btn=document.getElementById('dyDlBtn');
  bar.style.display='block';
  btn.textContent='下载中...';
  var timer=setInterval(async function(){
    try{
      var r=await fetch('/api/douyin/progress/'+gid);
      var s=await r.json();
      var pct=s.progress||0;
      var cm=(s.completed/1048576).toFixed(1);
      var tm=s.total?(s.total/1048576).toFixed(1):'?';
      var sp=s.speed?(s.speed/1048576).toFixed(2):'0';
      fill.style.width=Math.max(pct,3)+'%';
      fill.textContent=pct.toFixed(1)+'%';
      sizeEl.textContent=cm+' / '+tm+' MB';
      speedEl.textContent=sp+' MB/s';
      if(s.status==='complete'){
        clearInterval(timer);
        fill.style.width='100%';
        fill.textContent='100%';
        btn.textContent='开始下载';
        dyDlMsg('\u2705 视频下载完成！','ok');
      }else if(s.status==='error'||s.status==='removed'){
        clearInterval(timer);
        btn.textContent='开始下载';
        dyDlMsg('\u274c 下载失败: '+(s.msg||s.status),'err');
      }
    }catch(e){
      clearInterval(timer);
      btn.textContent='开始下载';
      dyDlMsg('进度查询失败: '+e.message,'err');
    }
  },1000);
}

function dyMsg(msg,cls){
  var el=document.getElementById('dyStatus');
  el.textContent=msg;
  el.style.color=cls==='err'?'var(--red)':cls==='ok'?'var(--green)':'var(--text2)';
}

function dyDlMsg(msg,cls){
  var el=document.getElementById('dyDlStatus');
  el.textContent=msg;
  el.style.color=cls==='err'?'var(--red)':cls==='ok'?'var(--green)':'var(--text2)';
}

function dyOpenSaveDir(){
  fetch('/api/douyin/open_dir').catch(function(){});
}

async function loadSF(){
  try{var r=await fetch('/api/settings');var s=await r.json();
    setVal('sap',s.aria2c_path);setVal('scd',s.comfyui_dir);setVal('spy',s.python_path);setVal('sgp',s.git_path);
    setVal('spm',s.pip_mirror||'default');setVal('sghm',s.github_mirror||'official');applyGithubMirrorLabels();
    setVal('shfc',s.hf_channel||'hf-mirror');
    // 注入开关不回读 hf_env_inject 本身，而是看 presets 里有没有 HF_ENDPOINT ——
    // 那个条目才是真正生效的东西（后端按「渠道+开关」自动增删它）
    (document.getElementById('shfEnv')||{}).checked=(s.comfyui_presets||[]).some(function(p){return (p.key||'')==='HF_ENDPOINT'});
    setVal('smc',s.max_concurrent_downloads);setVal('scl',s.max_concurrent_clones);
    setVal('smcs',s.max_connection_per_server);setVal('sspl',s.split);setVal('smss',s.min_split_size);
    setVal('sct',s.connect_timeout);setVal('sto',s.timeout);setVal('sls',s.lowest_speed_limit);
    setVal('srw',s.retry_wait);setVal('smt',s.max_tries);setVal('sfa',s.file_allocation);
    document.getElementById('scc').checked=s.check_certificate;document.getElementById('sas').checked=s.auto_start_aria2;
    document.getElementById('mcd').textContent=s.max_concurrent_downloads;document.getElementById('cld').textContent=s.max_concurrent_clones;
    // ComfyUI 启动命令
    setVal('scli',s.comfyui_listen);setVal('sclp',s.comfyui_port);
    document.getElementById('scla').checked=s.comfyui_auto_launch!==false;
    setVal('sclv',s.comfyui_vram||'default');setVal('sclpm',s.comfyui_preview_method||'default');
    setVal('sclc',s.comfyui_cors_origin);setVal('sclx',s.comfyui_extra_args);
    loadPresets(s.comfyui_presets||[]);
    setVal('soutd',s.output_dir||s.comfyui_dir+'\\output');
    setVal('sind',s.input_dir||s.comfyui_dir+'\\input');
    setVal('swfbd',s.workflow_backup_dir);
    setVal('swall',s.wallpaper_path);
    setVal('swallo',s.wallpaper_blur==null?0:s.wallpaper_blur);
    setVal('dyCookieFile',s.douyin_cookie_file);setVal('dySaveDirSet',s.douyin_save_dir);
    setVal('sdly',s.shutdown_delay==null?60:s.shutdown_delay);
    accLoad(s);
    cuiCmdPreview();applyWallpaper();applyPreset(s.theme_preset||'watercolor');
    auditLoad();   // 顺手刷新操作审计（S7）；失败只在审计卡片里显示，不影响设置表单
  }catch(e){}
}
function applyWallpaper(){
  var path=(gv('swall')||'').trim();
  var blur=gvi('swallo',0);
  var old=document.getElementById('wallpaper');
  if(old)old.remove();
  var hasWp=!!path;
  document.body.classList.toggle('has-wallpaper',hasWp);
  // 毛玻璃像素：0=无玻璃，1-10=像素。用 CSS 变量传给所有表面
  document.body.style.setProperty('--wp-blur',(blur>0?blur:0)+'px');
  if(!path){return}
  var lower=path.toLowerCase();
  var el;
  if(/\.(mp4|webm|mov|m4v)$/.test(lower)){
    el=document.createElement('video');
    el.autoplay=true;el.muted=true;el.loop=true;el.playsInline=true;
  }else{
    el=document.createElement('img');
  }
  el.id='wallpaper';
  el.src='/api/wallpaper';
  el.style.opacity='1';
  document.body.appendChild(el);
}
function setVal(id,v){var e=document.getElementById(id);if(e)e.value=(v===undefined||v===null)?'':v}
// ================= 配色主题预设（v5.1，与 data-theme 明暗正交） =================
// 三个层次：applyPreset 真正改 documentElement 的 data-preset（CSS 变量随之切换）；
// markPreset 只负责设置页 swatch 的 active 样式；pickPreset 是 onclick 入口，
// 点一下即时预览但不落盘 —— 保存设置时才写进 settings.json。
// 初值取服务端直出的 <html data-preset>（routes.py 渲染时带上）：万一 loadSF 的
// fetch 挂了，内存里的预设与页面实际配色、swatch 高亮也仍然一致
var _preset=(document.documentElement.getAttribute('data-preset'))||'watercolor';
function curPreset(){return _preset}
function applyPreset(p){
  _preset=(typeof p==='string'&&p)?p:'watercolor';
  // 一律写属性（含 watercolor）：状态可查（documentElement.dataset.preset），
  // 而 watercolor 没有对应 CSS 块，:root 基座就是它，行为与不写属性完全一致
  document.documentElement.setAttribute('data-preset',_preset);
  markPreset(_preset);
}
function markPreset(p){
  var chips=document.querySelectorAll('.preset-chip');
  for(var i=0;i<chips.length;i++){
    chips[i].classList.toggle('active',chips[i].getAttribute('data-p')===p);
  }
}
function pickPreset(btn){applyPreset((btn&&btn.getAttribute('data-p'))||'watercolor')}
async function saveS(){
  var d={aria2c_path:gv('sap'),comfyui_dir:gv('scd'),python_path:gv('spy'),git_path:gv('sgp'),
    max_concurrent_downloads:gvi('smc',3),max_concurrent_clones:gvi('scl',2),
    max_connection_per_server:gvi('smcs',8),split:gvi('sspl',8),min_split_size:gv('smss')||'10M',
    connect_timeout:gvi('sct',60),timeout:gvi('sto',60),lowest_speed_limit:gv('sls')||'1K',
    retry_wait:gvi('srw',3),max_tries:gvi('smt',0),file_allocation:gv('sfa'),
    check_certificate:document.getElementById('scc').checked,auto_start_aria2:document.getElementById('sas').checked,
    comfyui_listen:gv('scli'),comfyui_port:gv('sclp'),
    comfyui_auto_launch:document.getElementById('scla').checked,
    comfyui_vram:gv('sclv')||'default',comfyui_preview_method:gv('sclpm')||'default',
    comfyui_cors_origin:gv('sclc'),comfyui_extra_args:gv('sclx'),
    comfyui_presets:collectPresets(),
    input_dir:gv('sind'),output_dir:gv('soutd'),workflow_backup_dir:gv('swfbd'),
    wallpaper_path:gv('swall'),wallpaper_blur:gvi('swallo',0),theme_preset:curPreset(),
    pip_mirror:gv('spm')||'default',github_mirror:gv('sghm')||'official',hf_channel:gv('shfc')||'hf-mirror',hf_env_inject:!!(document.getElementById('shfEnv')||{}).checked,douyin_cookie_file:gv('dyCookieFile'),douyin_save_dir:gv('dySaveDirSet'),shutdown_delay:gviClamp('sdly',60,0,3600),web_host:_accV('swh')||'0.0.0.0',web_port:_accI('swp',7860),access_token:_accV('stok'),token_required:!!(document.getElementById('stokreq')||{}).checked,max_upload_gb:gvf('smu',8)};
  try{var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});var j=await r.json();
    if(j.ok){document.getElementById('mcd').textContent=d.max_concurrent_downloads;document.getElementById('cld').textContent=d.max_concurrent_clones;cuiCmdPreview();applyWallpaper();repairLoadMirrorInfo();accStatus();notify(j.msg)}else{notify('保存失败: '+j.msg)}}catch(e){notify('保存失败: '+e.message)}
}
function gv(id){return document.getElementById(id).value.trim()}
function gvi(id,d){return parseInt(document.getElementById(id).value)||d}
function gviClamp(id,d,lo,hi){var v=parseInt(document.getElementById(id).value,10);if(isNaN(v))return d;return Math.max(lo,Math.min(hi,v))}
function gvf(id,d){var v=parseFloat(document.getElementById(id).value);return isNaN(v)?d:v}
// ================= 管理器访问（监听地址 / 端口 / 访问令牌） =================
var _accEff={},_accCli={};
function _accEsc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function _accV(id){var e=document.getElementById(id);return e?(e.value||'').trim():''}
function _accI(id,d){var v=parseInt(_accV(id),10);return isNaN(v)?d:v}
function accLoad(s){
  s=s||{};_accEff=s._effective||{};_accCli=s._client||{};
  setVal('swh',s.web_host||'0.0.0.0');
  setVal('swp',s.web_port==null?7860:s.web_port);
  setVal('stok',s.access_token||'');
  setVal('smu',s.max_upload_gb==null?8:s.max_upload_gb);
  var cb=document.getElementById('stokreq');if(cb)cb.checked=s.token_required!==false;
  accStatus();
}
function accStatus(){
  var box=document.getElementById('accStatus');if(!box)return;
  var eff=_accEff||{},cli=_accCli||{},rows=[],badge=document.getElementById('accBadge');
  var cb=document.getElementById('stokreq');
  if(eff.running){rows.push('当前实际监听：<b>'+_accEsc(eff.host)+':'+_accEsc(eff.port)+'</b>')}
  else{rows.push('<span style="color:var(--text3)">当前实际监听：未知（本次不是由 run.py 启动的）</span>')}
  rows.push('令牌校验：'+(cb&&cb.checked?'<b>已开启</b>':'<span style="color:var(--amber2)">已关闭</span>'));
  rows.push(cli.local?'你这次是从 <b>本机</b> 访问的，不需要令牌':'你这次是从 <b>'+_accEsc(cli.ip||'?')+'</b> 访问的，需要令牌');
  var pending=false;
  if(eff.running){
    var wH=_accV('swh')||'0.0.0.0',wP=_accI('swp',7860);
    pending=(String(wH)!==String(eff.host)||Number(wP)!==Number(eff.port));
  }
  if(pending){rows.push('<span style="color:var(--amber2)">监听地址 / 端口有改动，保存后需要「退出管理器」再重新启动才生效</span>')}
  if(badge)badge.style.display=pending?'inline-flex':'none';
  box.innerHTML=rows.join('<br>');
}
function _accCopyText(t,okMsg){
  if(!t){notify('没有可复制的内容');return}
  var done=function(){notify(okMsg)};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(done,function(){_accCopyFallback(t,okMsg)});
  }else{_accCopyFallback(t,okMsg)}
}
function _accCopyFallback(t,okMsg){
  var ta=document.createElement('textarea');ta.value=t;
  ta.style.position='fixed';ta.style.top='-1000px';document.body.appendChild(ta);
  ta.select();
  try{document.execCommand('copy');notify(okMsg)}catch(e){notify('复制失败，请手动选中后复制')}
  document.body.removeChild(ta);
}
function accCopy(){_accCopyText(_accV('stok'),'访问令牌已复制')}
function accCopyLink(){
  var t=_accV('stok');if(!t){notify('没有可复制的令牌');return}
  var p=_accI('swp',7860),h=_accV('swh')||'0.0.0.0',ips=[];
  if(h==='0.0.0.0'){
    ips=((_accEff&&_accEff.lan_ips)||[]).slice();
    if(!ips.length){var hn=location.hostname;if(hn&&hn!=='127.0.0.1'&&hn!=='localhost')ips.push(hn)}
  }
  var wan=(h==='0.0.0.0'&&ips.length>0);
  var link=wan?('http://'+ips[0]+':'+p+'/?t='+encodeURIComponent(t)):('http://127.0.0.1:'+p+'/');
  _accCopyText(link,wan?'直链已复制，发给另一台设备即可直接打开':'直链已复制（仅本机可打开，本机免令牌）');
}
async function accRegen(){
  if(!(await confirmDialog('确认重新生成访问令牌？\n\n旧令牌会立即失效，已经打开本管理器的其它设备需要重新输入新令牌。',{title:'重新生成访问令牌',okText:'重新生成',danger:true})))return;
  try{
    var r=await fetch('/api/settings/regenerate_token',{method:'POST'});
    var j=await r.json();
    if(j.ok){setVal('stok',j.access_token||'');accStatus();notify(j.msg||'已重新生成访问令牌')}
    else{notify('失败：'+(j.msg||''))}
  }catch(e){notify('失败：'+e.message)}
}
async function accQuit(){
  if(!(await confirmDialog('确认退出管理器？\n\n这个页面会断开连接。想重新使用时，请到管理器所在电脑上双击启动器。\n（只关闭管理器，ComfyUI 本体不受影响）',{title:'退出管理器',okText:'退出',danger:true})))return;
  try{await fetch('/api/system/quit',{method:'POST'})}catch(e){}
  notify('管理器正在退出…');
  setTimeout(function(){
    document.body.innerHTML='<div style="padding:80px 20px;text-align:center;'
      +'font:15px/2 -apple-system,\'Segoe UI\',\'Microsoft YaHei\',sans-serif;color:#6b7280">'
      +'管理器已退出，可以关闭这个页面了。</div>';
  },900);
}
// ---- GitHub 镜像连通性测试 ----
var _ghmResults=[];
function ghMirrorPick(k){
  document.getElementById('sghm').value=k;
  var lb='';for(var i=0;i<_ghmResults.length;i++){if(_ghmResults[i].key===k)lb=_ghmResults[i].label}
  applyGithubMirrorLabels();
  notify('已选择：'+(lb||k)+'，点「保存设置」后生效');
}
function applyGithubMirrorLabels(){
  var M=GH_MIRROR_LABELS;
  var el0=document.getElementById('sghm');
  var k=(el0&&el0.value)||'official';
  if(typeof repairLoadMirrorInfo==='function')repairLoadMirrorInfo();
}
function renderGhMirrorResult(list,current){
  var box=document.getElementById('ghmResult');
  var best=null;list.forEach(function(x){if((x.status==='ok'||x.status==='slow')&&(!best||x.ms<best.ms))best=x});
  var h='<div style="border:1px solid var(--border);border-radius:10px;overflow:hidden">';
  h+='<div style="display:flex;gap:10px;padding:7px 12px;font-size:11px;color:var(--text3);background:rgba(74,111,165,0.04)"><span style="flex:0 0 140px">镜像</span><span style="flex:0 0 62px">类型</span><span style="flex:0 0 84px">状态</span><span style="flex:0 0 66px">延迟</span><span style="flex:1 1 auto">说明</span><span style="flex:0 0 50px;text-align:right">操作</span></div>';
  list.forEach(function(x){
    var col=x.status==='ok'?'var(--green)':(x.status==='slow'?'var(--amber)':'var(--red)');
    var ico=x.status==='ok'?'bi-check-circle-fill':(x.status==='slow'?'bi-exclamation-triangle-fill':'bi-x-circle-fill');
    var txt=x.status==='ok'?'可用':(x.status==='slow'?'较慢':'不可用');
    var sel=x.key===current;
    h+='<div style="display:flex;gap:10px;align-items:center;padding:8px 12px;font-size:12px;border-top:1px solid var(--border)'+(sel?';background:rgba(74,111,165,0.06)':'')+'">';
    var rec=(best&&x.key===best.key&&list.length>1);
    h+='<span style="flex:0 0 140px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(x.label)+(rec?' <span style="font-size:10px;color:var(--green)">推荐</span>':'')+(sel?' <span style="font-size:10px;color:var(--cyan)">当前</span>':'')+'</span>';
    h+='<span style="flex:0 0 62px;color:var(--text3);font-size:11px">'+esc(x.group)+'</span>';
    h+='<span style="flex:0 0 84px;color:'+col+';font-size:11px;white-space:nowrap"><i class="bi '+ico+'"></i> '+txt+'</span>';
    h+='<span style="flex:0 0 66px;color:var(--text2);font-size:11px">'+(x.ms?x.ms+' ms':'-')+'</span>';
    h+='<span style="flex:1 1 auto;color:var(--text3);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escAttr(x.msg||'')+'">'+esc(x.msg||'')+'</span>';
    h+='<span style="flex:0 0 50px;text-align:right">'+(sel?'':'<button class="btn btn6 btn-xs" onclick="ghMirrorPick(&quot;'+x.key+'&quot;)">选用</button>')+'</span>';
    h+='</div>';
  });
  h+='</div>';
  h+='<div style="margin-top:8px;font-size:11px;color:var(--text3);line-height:1.8">'
    +'探测目标：octocat/Hello-World 仓库的 git 端点（等价于 git clone 的第一步），延迟越低越好；结果仅代表当前网络环境。<br>'
    +'若某个镜像始终不可用，说明它目前无法直接用于 git 克隆（例如 github.akams.cn 是节点门户，其加速节点为另外的域名），换其他镜像即可。'
    +'</div>';
  box.innerHTML=h;
}
async function ghMirrorTest(){
  var btn=document.getElementById('ghmTestBtn'),box=document.getElementById('ghmResult');
  if(!btn||btn.disabled)return;
  btn.disabled=true;btn.innerHTML='<i class="bi bi-hourglass-split"></i> 测试中...';
  box.style.display='block';
  box.innerHTML='<div style="font-size:12px;color:var(--text2);padding:8px 2px"><i class="bi bi-arrow-repeat"></i> 正在并发测试各镜像的 git 连通性，请稍候…</div>';
  try{
    var r=await fetch('/api/github/mirror_test');var j=await r.json();
    if(!j.ok){box.innerHTML='<div style="font-size:12px;color:var(--red);padding:8px 2px">'+esc(j.msg||'测试失败')+'</div>';return}
    _ghmResults=j.results||[];
    renderGhMirrorResult(_ghmResults,j.current||'official');
  }catch(e){
    box.innerHTML='<div style="font-size:12px;color:var(--red);padding:8px 2px">测试失败: '+esc(e.message)+'</div>';
  }finally{
    btn.disabled=false;btn.innerHTML='<i class="bi bi-activity"></i> 连通性测试';
  }
}
async function ghMirrorFastest(){
  var btn=document.getElementById('ghmFastBtn'),box=document.getElementById('ghmResult');
  if(!btn||btn.disabled)return;
  btn.disabled=true;btn.innerHTML='<i class="bi bi-hourglass-split"></i> 测速中...';
  box.style.display='block';
  box.innerHTML='<div style="font-size:12px;color:var(--text2);padding:8px 2px"><i class="bi bi-arrow-repeat"></i> 正在并发测速各镜像，稍后自动选用最快的一个…</div>';
  try{
    var r=await fetch('/api/github/mirror_fastest');var j=await r.json();
    if(!j.ok){
      // 探测失败或全军覆没：有明细就摊开给用户自己选，没有就只报错
      if(j.results&&j.results.length){_ghmResults=j.results;renderGhMirrorResult(_ghmResults,(document.getElementById('sghm')||{}).value||'official')}
      else{box.innerHTML='<div style="font-size:12px;color:var(--red);padding:8px 2px">'+esc(j.msg||'测速失败')+'</div>'}
      return;
    }
    // 后端已把最快镜像原地写进 settings 并落盘，这里只同步界面 + 重绘结果表
    document.getElementById('sghm').value=j.key;
    applyGithubMirrorLabels();
    if(j.results&&j.results.length){_ghmResults=j.results;renderGhMirrorResult(_ghmResults,j.key)}
    else{box.innerHTML='<div style="font-size:12px;color:var(--green);padding:8px 2px"><i class="bi bi-check-circle-fill"></i> '+esc(j.msg||('已选用 '+j.label))+'（'+(j.ms||0)+' ms），已自动保存</div>'}
    notify('已选用 '+(j.label||j.key)+'（'+(j.ms||0)+' ms），已自动保存');
  }catch(e){
    box.innerHTML='<div style="font-size:12px;color:var(--red);padding:8px 2px">测速失败: '+esc(e.message)+'</div>';
  }finally{
    btn.disabled=false;btn.innerHTML='<i class="bi bi-lightning-charge"></i> 测速选最快';
  }
}
// ---- 操作审计（S7，v5.1）----
// 只读展示：写入全在各蓝线的危险动作处埋点（svc.audit），这里不提供写入口。
var _auditTimer=null;
function auditDebounce(){clearTimeout(_auditTimer);_auditTimer=setTimeout(auditLoad,300)}
async function auditLoad(){
  var box=document.getElementById('auditBox');if(!box)return;
  box.innerHTML='<div style="font-size:12px;color:var(--text2);padding:8px 2px"><i class="bi bi-arrow-repeat"></i> 正在读取审计记录…</div>';
  try{
    var act=document.getElementById('auditAction'),kw=document.getElementById('auditKw');
    var q='?limit=200';
    if(act&&act.value)q+='&action='+encodeURIComponent(act.value);
    if(kw&&kw.value.trim())q+='&kw='+encodeURIComponent(kw.value.trim());
    var j=await api('/api/audit'+q);
    // 动作下拉第一次拿到字典时填充（之后 options 已存在，不再重建）
    if(act&&j.actions&&act.options.length<=1){
      Object.keys(j.actions).forEach(function(k){
        var o=document.createElement('option');o.value=k;o.textContent=j.actions[k];act.appendChild(o);
      });
    }
    renderAudit(j.logs||[],j.actions||{});
  }catch(e){
    box.innerHTML='<div style="font-size:12px;color:var(--red);padding:8px 2px">读取失败: '+esc(e.message)+'</div>';
  }
}
function renderAudit(list,labels){
  var box=document.getElementById('auditBox');
  if(!list.length){box.innerHTML='<div style="font-size:12px;color:var(--text3);padding:8px 2px">暂无记录</div>';return}
  var h='<div style="border:1px solid var(--border);border-radius:10px;overflow:hidden">';
  h+='<div style="display:flex;gap:10px;padding:6px 12px;font-size:11px;color:var(--text3);background:rgba(74,111,165,0.04)"><span style="flex:0 0 138px">时间</span><span style="flex:0 0 96px">动作</span><span style="flex:0 0 260px">对象</span><span style="flex:1 1 auto">详情</span><span style="flex:0 0 92px;text-align:right">来源</span></div>';
  list.forEach(function(x){
    var lab=labels[x.action]||x.action;
    var col=x.ok?'var(--green)':'var(--red)';
    var ico=x.ok?'bi-check-circle-fill':'bi-x-circle-fill';
    h+='<div style="display:flex;gap:10px;align-items:baseline;padding:7px 12px;font-size:12px;border-top:1px solid var(--border)">'
      +'<span style="flex:0 0 138px;color:var(--text3);font-size:11px;font-variant-numeric:tabular-nums">'+esc(x.ts||'')+'</span>'
      +'<span style="flex:0 0 96px;color:'+col+';white-space:nowrap"><i class="bi '+ico+'"></i> '+esc(lab)+'</span>'
      +'<span style="flex:0 0 260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escAttr(x.target||'')+'">'+esc(x.target||'')+'</span>'
      +'<span style="flex:1 1 auto;color:var(--text3);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+escAttr(x.detail||'')+'">'+esc(x.detail||'')+'</span>'
      +'<span style="flex:0 0 92px;color:var(--text3);font-size:11px;text-align:right">'+esc(x.ip||'')+'</span>'
      +'</div>';
  });
  h+='</div>';
  box.innerHTML=h;
}
// 环境变量预设
function collectPresets(){var r=[];document.querySelectorAll('#presetList .pr-row').forEach(function(row){var k=row.querySelector('.pr-key'),v=row.querySelector('.pr-val');if(k&&v){var key=k.value.trim(),val=v.value.trim();if(key)r.push({key:key,value:val})}});return r}
function addPreset(k,v){k=k||'';v=v||'';var d=document.createElement('div');d.className='pr-row';d.innerHTML='<input class="pr-key sinput" value="'+esc(k)+'" placeholder="变量名" style="width:200px;font-size:13px;padding:4px 8px;"><span style="margin:0 4px;color:#888;align-self:center;font-size:13px;">=</span><input class="pr-val sinput" value="'+esc(v)+'" placeholder="值" style="width:140px;font-size:13px;padding:4px 8px;"><button class="btn btn-xs btn-danger" onclick="this.closest(\'.pr-row\').remove();cuiCmdPreview()" style="margin-left:6px;"><i class="bi bi-x"></i> 删除</button>';d.querySelectorAll('input').forEach(function(e){e.addEventListener('input',cuiCmdPreview)});document.getElementById('presetList').appendChild(d);cuiCmdPreview()}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function loadPresets(arr){document.getElementById('presetList').innerHTML='';if(arr&&arr.length)arr.forEach(function(p){addPreset(p.key,p.value)})}
async function saveSRestart(){await saveS();await new Promise(function(r){setTimeout(r,600)});await a2Restart()}
async function resetS(){if(!(await confirmDialog('确认恢复默认？')))return;try{var r=await fetch('/api/settings/reset',{method:'POST'});var j=await r.json();j.ok&&(notify(j.msg),loadSF())}catch(e){}}
async function cuiCmdReset(){if(!(await confirmDialog('确认重置 ComfyUI 启动命令为最简单的默认模式？(python main.py --auto-launch)')))return;
  try{var r=await fetch('/api/comfyui/cmd_reset',{method:'POST'});var j=await r.json();j.ok&&(notify(j.msg),loadSF())}catch(e){}}
async function cuiCmdPreview(){
  try{var r=await fetch('/api/comfyui/cmd_preview');var j=await r.json();
    var e=document.getElementById('cuiCmdPreview');if(e)e.textContent='命令: '+j.cmd_str
  }catch(e){}}
