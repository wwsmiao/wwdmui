// ============================================================
// 全局设置 / 管理器访问（v6.4：抖音功能已下线，设置逻辑独立为本文件）
// ============================================================

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
    _wallPath=s.wallpaper_path||'';
    _wallBlur=(s.wallpaper_blur==null?0:s.wallpaper_blur);
    accLoad(s);
    cuiCmdPreview();applyWallpaper();applyPreset(s.theme_preset||'watercolor');
  }catch(e){}
}
function applyWallpaper(path,blur){
  if(path!==undefined)_wallPath=path||'';
  if(blur!==undefined)_wallBlur=parseInt(blur)||0;
  var p=(_wallPath||'').trim();
  var b=_wallBlur||0;
  var old=document.getElementById('wallpaper');
  if(old)old.remove();
  var hasWp=!!p;
  document.body.classList.toggle('has-wallpaper',hasWp);
  // 毛玻璃像素：0=无玻璃，1-10=像素。用 CSS 变量传给所有表面
  document.body.style.setProperty('--wp-blur',(b>0?b:0)+'px');
  if(!p){return}
  var lower=p.toLowerCase();
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
var _wallPath='',_wallBlur=0;   // v6.4：壁纸/毛玻璃改由悬浮球右键配置
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
    pip_mirror:gv('spm')||'default',github_mirror:gv('sghm')||'official',hf_channel:gv('shfc')||'hf-mirror',hf_env_inject:!!(document.getElementById('shfEnv')||{}).checked,web_host:_accV('swh')||'0.0.0.0',web_port:_accI('swp',7860),access_token:_accV('stok'),token_required:!!(document.getElementById('stokreq')||{}).checked,max_upload_gb:gvf('smu',8)};
  try{var r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});var j=await r.json();
    if(j.ok){document.getElementById('mcd').textContent=d.max_concurrent_downloads;document.getElementById('cld').textContent=d.max_concurrent_clones;cuiCmdPreview();applyWallpaper();repairLoadMirrorInfo();accStatus();notify(j.msg)}else{notify('保存失败: '+j.msg)}}catch(e){notify('保存失败: '+e.message)}
}
function gv(id){var e=document.getElementById(id);return e?e.value.trim():''}
function gvi(id,d){var e=document.getElementById(id);if(!e)return d;var v=parseInt(e.value);return isNaN(v)?d:v}
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
  h+='<div style="display:flex;gap:10px;padding:7px 12px;font-size:11px;color:var(--text3);background:color-mix(in srgb,var(--cyan) 4%,transparent)"><span style="flex:0 0 140px">镜像</span><span style="flex:0 0 62px">类型</span><span style="flex:0 0 84px">状态</span><span style="flex:0 0 66px">延迟</span><span style="flex:1 1 auto">说明</span><span style="flex:0 0 50px;text-align:right">操作</span></div>';
  list.forEach(function(x){
    var col=x.status==='ok'?'var(--green)':(x.status==='slow'?'var(--amber)':'var(--red)');
    var ico=x.status==='ok'?'bi-check-circle-fill':(x.status==='slow'?'bi-exclamation-triangle-fill':'bi-x-circle-fill');
    var txt=x.status==='ok'?'可用':(x.status==='slow'?'较慢':'不可用');
    var sel=x.key===current;
    h+='<div style="display:flex;gap:10px;align-items:center;padding:8px 12px;font-size:12px;border-top:1px solid var(--border)'+(sel?';background:color-mix(in srgb,var(--cyan) 6%,transparent)':'')+'">';
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
  h+='<div style="display:flex;gap:10px;padding:6px 12px;font-size:11px;color:var(--text3);background:color-mix(in srgb,var(--cyan) 4%,transparent)"><span style="flex:0 0 138px">时间</span><span style="flex:0 0 96px">动作</span><span style="flex:0 0 260px">对象</span><span style="flex:1 1 auto">详情</span><span style="flex:0 0 92px;text-align:right">来源</span></div>';
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
