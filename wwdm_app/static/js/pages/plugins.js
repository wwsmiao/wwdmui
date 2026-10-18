
// ============================================================
// 插件管理（CRUD + 克隆）
// ============================================================
var plData=[],plPage=1,plPageSize=20,plTotalPages=1,plTotal=0;
var plActiveIds=new Set(),plQueuedIds=new Set(),plSelIds=new Set(),plProgTimer=null,plLastStates={},plPrevActive=new Set(),plCancelledIds=new Set(),plProgSeen={},plQueuedSeen={},plKnownNames={},plMirrorInfo=null;

async function loadPlugins(){var q=document.getElementById('ps').value.trim(),sort=document.getElementById('plsort').value;var params='?page='+plPage+'&page_size='+plPageSize+'&sort='+sort;if(sort==='installed')params+='&installed=1';if(q)params+='&q='+encodeURIComponent(q);try{var r=await fetch('/api/plugins'+params);var j=await r.json();plData=j.items||[];plTotal=j.total||0;plPage=j.page||1;plTotalPages=j.total_pages||1;renderPlugins();updatePlPagination();document.getElementById('plmcount').textContent='共 '+plTotal+' 条'}catch(e){}}
function renderPlugins(){
  var tb=document.getElementById('pltbody');tb.innerHTML='';
  var _h=[];
  plData.forEach(function(p){
    var ch=plSelIds.has(p.id)?'checked':'',st=plLastStates[p.id]||{},stat=st.status||'none';
    var inst=p.installed?'<span class="badge bg-inst" style="margin-left:4px">已安装</span>':'';
    var urlDisp=p.repo_url.substring(0,40)+(p.repo_url.length>40?'...':'');
    var actions='<div style="display:flex;gap:3px;flex-wrap:wrap">'+
      '<button class="btn btn4 btn-xs" onclick="editPlugin('+p.id+')"><i class="bi bi-pencil"></i> 编辑</button>'+
      (p.installed?'<button class="btn btn3 btn-xs" onclick="delPlugin('+p.id+')"><i class="bi bi-trash"></i> 卸载</button>':'<button class="btn btn3 btn-xs" disabled style="opacity:0.35"><i class="bi bi-trash"></i> 卸载</button>')+
      clBtns(p.id,stat,p.installed)+'</div>';
    _h.push('<tr id="plr-'+p.id+'"><td class="cb-cell"><input type="checkbox" class="plcb" value="'+p.id+'" '+ch+' onchange="plOnSel('+p.id+',this.checked)"></td><td>'+p.id+'</td><td>'+esc(p.name)+inst+'</td><td><a href="'+p.repo_url+'" target="_blank" style="font-size:11px">'+esc(urlDisp)+'</a></td><td style="color:var(--text2);font-size:11.5px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(p.desc||'')+'">'+esc(p.desc||'')+'</td><td id="plst-'+p.id+'">'+clBadge(stat)+'</td><td class="acts">'+actions+'</td></tr>')
  });
  if(_h.length)tb.innerHTML+=_h.join('');
  plUpCnt()
}
function updatePlPagination(){document.getElementById('plinfo').textContent='第 '+plPage+' / '+plTotalPages+' 页';document.getElementById('plprev').disabled=plPage<=1;document.getElementById('plnext').disabled=plPage>=plTotalPages}
function openPluginModal(){document.getElementById('pmtitle').textContent='新增插件';document.getElementById('peid').value='';document.getElementById('pfname').value='';document.getElementById('pfurl').value='';document.getElementById('pfdesc').value='';document.getElementById('pmbg').classList.add('show')}
async function editPlugin(id){try{var r=await fetch('/api/plugins/'+id);var p=await r.json();if(!p||!p.id)return;document.getElementById('pmtitle').textContent='编辑 #'+id;document.getElementById('peid').value=id;document.getElementById('pfname').value=p.name;document.getElementById('pfurl').value=p.repo_url||p.url;document.getElementById('pfdesc').value=p.desc||p.description||'';document.getElementById('pmbg').classList.add('show')}catch(e){notify('加载失败')}}
function closePluginModal(){document.getElementById('pmbg').classList.remove('show');loadPlugins()}
function resetPlugins(){
  document.getElementById('ps').value='';
  document.getElementById('plsort').value='id_asc';
  plPage=1;loadPlugins();
}
async function openPluginDir(){
  try{await fetch('/api/plugins/open_dir')}catch(e){}
}
async function savePlugin(){
  var id=document.getElementById('peid').value,b={name:document.getElementById('pfname').value.trim(),url:document.getElementById('pfurl').value.trim(),description:document.getElementById('pfdesc').value.trim()};
  if(!b.name||!b.url){notify('请填写完整');return}
  try{if(id){await fetch('/api/plugins/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}else{var r=await fetch('/api/plugins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});var j=await r.json();if(!j.ok){notify(j.msg);return}}closePluginModal()}catch(e){notify('保存失败: '+e.message)}
}
async function delPlugin(id){if(!(await confirmDialog('确认卸载该插件？\n\n仅删除 custom_nodes 下的插件文件夹，不会删除插件库中的记录，之后仍可重新克隆。',{danger:true,title:'卸载插件'})))return;try{var r=await fetch('/api/plugins/'+id,{method:'DELETE'});var d=await r.json();if(!d.ok){notify(d.error||'卸载失败')}else{notify(d.msg||'已卸载')};loadPlugins()}catch(e){notify('卸载失败: '+e)}}

// ---- 克隆控制 ----
function clBadge(s){var m={cloning:'bg-cloning|克隆中',complete:'bg-done|已完成',error:'bg-err|错误',queue:'bg-q|排队中'},p=(m[s]||'bg-off|未开始').split('|');return'<span class="badge '+p[0]+'">'+p[1]+'</span>'}
function clBtns(id,s,installed){var done=s==='complete'||installed;return'<button class="btn btn-teal btn-xs" id="cbclone-'+id+'" onclick="clStart('+id+')"'+(done?' disabled':'')+'>'+(installed?'<i class="bi bi-check-lg"></i> 已安装':s==='complete'?'<i class="bi bi-check-lg"></i> 已完成':'<i class="bi bi-download"></i> 克隆')+'</button><button class="btn btn3 btn-xs" id="cbcancel-'+id+'" onclick="clCancel('+id+')"'+(s==='cloning'||s==='queue'?'':' disabled')+'><i class="bi bi-x-lg"></i> 取消</button>'}
function plOnSel(id,c){if(c)plSelIds.add(id);else plSelIds.delete(id);plUpCnt()}
function plSelAll(c){if(c){plData.forEach(function(p){plSelIds.add(p.id)})}else{plSelIds.clear()}renderPlugins()}
function plUpCnt(){var e=document.getElementById('pbcnt');if(e)e.textContent='已选 '+plSelIds.size}

async function checkGit(){try{var r=await fetch('/api/git/detect');var d=await r.json();var dot=document.getElementById('gitdot'),t=document.getElementById('gittext');if(d.found){dot.className='a2dot on';t.textContent='Git '+esc(d.version||'')}else{dot.className='a2dot off';t.textContent='Git 未找到'}return d.found}catch(e){return false}}
async function detectGit(){var d=document.getElementById('gtr');d.innerHTML='<span style="color:var(--text3);font-size:12px">检测中...</span>';try{var r=await fetch('/api/git/detect');var j=await r.json();if(j.found){document.getElementById('sgp').value=j.path;d.innerHTML='<span class="db db-ok">'+esc(j.version||j.path)+'</span>'}else{d.innerHTML='<span class="db db-fail">未找到 Git</span>'}}catch(e){d.innerHTML='<span class="db db-fail">检测失败</span>'}}
async function detectPy(){var d=document.getElementById('pyr');d.innerHTML='<span style="color:var(--text3);font-size:12px">检测中...</span>';try{var r=await fetch('/api/python/detect');var j=await r.json();if(j.found){document.getElementById('spy').value=j.path;d.innerHTML='<span class="db db-ok">'+esc(j.version||j.path)+'</span>'}else{d.innerHTML='<span class="db db-fail">未找到 Python</span>'}}catch(e){d.innerHTML='<span class="db db-fail">检测失败</span>'}}
async function batchClone(){if(plSelIds.size===0){notify('请先勾选插件');return}if(!await checkGit()){notify('Git 未找到，请在设置中配置');return}var ids=Array.from(plSelIds);try{var r=await fetch('/api/plugins/clone_batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:ids})});var j=await r.json();if(!j.ok)return;var s=0,q=0,names={};plData.forEach(function(p){names[p.id]=p.name});j.results.forEach(function(x){x.ok&&(x.msg.indexOf('队列')>=0?q++:s++)});plLogBanner();plLogAppend('批量克隆：共 '+ids.length+' 个（直接开始 '+s+' / 排队 '+q+'）');j.results.forEach(function(x){if(!x.ok)plLogAppend('  x '+(names[x.id]||('#'+x.id))+'  '+x.msg)});notify('批量克隆: 开始 '+s+' 队列 '+q);plSelIds.clear();renderPlugins();plStartTimer();await restoreCloneState()}catch(e){notify('批量克隆失败: '+e.message)}}
async function clStart(id){if(!await checkGit()){notify('Git 未找到');return}try{var r=await fetch('/api/plugins/clone/'+id,{method:'POST'});var j=await r.json();if(!j.ok){notify(j.msg);return}var q=j.msg.indexOf('队列')>=0;if(q){plQueuedIds.add(id)}else{plActiveIds.add(id)}plLastStates[id]={status:q?'queue':'cloning',progress:0};plLogStart(id,j.msg);renderPlugins();plStartTimer()}catch(e){notify('克隆失败')}}
async function clCancel(id){if(!(await confirmDialog('确认取消？')))return;plCancelledIds.add(id);plLogOpen();if(plQueuedIds.has(id)){try{await fetch('/api/plugins/clone_cancel_queue/'+id,{method:'POST'});plQueuedIds.delete(id);delete plLastStates[id];renderPlugins();plLogAppend('… '+(plKnownNames[id]||('#'+id))+'  已从队列移除')}catch(e){}return}try{var r=await fetch('/api/plugins/clone_cancel/'+id,{method:'POST'});var j=await r.json();if(j.ok){plActiveIds.delete(id);delete plLastStates[id];renderPlugins();plLogAppend('x '+(plKnownNames[id]||('#'+id))+'  已取消克隆')}}catch(e){}}
function plStartTimer(){if(!plProgTimer)plProgTimer=setInterval(fetchClAllStatus,2000)}
async function fetchClAllStatus(){
  try{var r=await fetch('/api/plugins/clone_all_status');var d=await r.json(),any=false,nowActive=new Set();
    d.active.forEach(function(x){nowActive.add(x.id);any=true;plKnownNames[x.id]=x.name||plKnownNames[x.id];plLastStates[x.id]={status:x.status,progress:x.progress||0,phase:x.phase,msg:x.msg};clUpdateCell(x.id,x)});
    plPrevActive.forEach(function(id){
      if(!nowActive.has(id)&&!plQueuedIds.has(id)){
        var nm=plKnownNames[id]||('#'+id);
        plActiveIds.delete(id);
        if(plCancelledIds.has(id)){plCancelledIds.delete(id)}
        else if(!plLastStates[id]||plLastStates[id].status!=='error'){plLastStates[id]={status:'complete',progress:100};plLogAppend('v '+nm+'  克隆完成')}
        else{plLogAppend('x '+nm+'  克隆失败：'+((plLastStates[id]||{}).msg||''))}
        renderPlugins()
      }
    });
    plPrevActive=new Set(nowActive);
    d.queue&&d.queue.length>0&&(any=true);
    plLogSync(d.active,d.queue);
    if(!any){clearInterval(plProgTimer);plProgTimer=null;plLogStatus('空闲')}
  }catch(e){}
}

// ============================================================
// v6.4 本地插件：双击代码文件 → 行号编辑器（可保存）；图片/其他 → 直出预览
// ============================================================
var _lpCodeRel = '', _lpCodeScope = 'plugins';

function lpIsImg(rel) {
  return /\.(png|jpe?g|gif|webp|bmp|svg|ico|avif)$/i.test(rel || '');
}
function lpIsText(rel) {
  return /\.(py|json|txt|md|ya?ml|toml|ini|cfg|js|css|html?|xml|csv|log|bat|cmd|ps1|sh|gitignore|gitattributes|env|sql)$/i.test(rel || '');
}
function lpOpenRaw(rel, scope) {
  window.open('/api/models/local/raw?scope=' + encodeURIComponent(scope || 'plugins')
    + '&rel=' + encodeURIComponent(rel), '_blank');
}
function lpOnFileDblClick(rel, scope) {
  if (!rel) return;
  if (lpIsImg(rel)) { lpOpenRaw(rel, scope); return; }
  if (lpIsText(rel)) { lpCodeOpen(rel, scope); return; }
  lpOpenRaw(rel, scope);
}
function lpCodeSync() {
  var ta = document.getElementById('lpCode');
  var no = document.getElementById('lpCodeNo');
  if (!ta || !no) return;
  var n = ta.value.split('\n').length;
  var arr = [];
  for (var i = 1; i <= n; i++) arr.push(i);
  no.textContent = arr.join('\n');
  no.scrollTop = ta.scrollTop;
}
function lpCodeOpen(rel, scope) {
  _lpCodeRel = rel || '';
  _lpCodeScope = scope || 'plugins';
  var m = document.getElementById('lpCodeModal');
  if (!m) return;
  m.style.display = 'flex';
  document.getElementById('lpCodeTitle').textContent = '编辑：' + _lpCodeRel;
  document.getElementById('lpCodeMsg').textContent = '加载中…';
  document.getElementById('lpCode').value = '';
  document.getElementById('lpCodeNo').textContent = '';
  fetch('/api/models/local/text?scope=' + encodeURIComponent(_lpCodeScope)
    + '&rel=' + encodeURIComponent(_lpCodeRel))
    .then(function (r) { return r.json(); })
    .then(function (j) {
      if (!j.ok) {
        document.getElementById('lpCodeMsg').textContent = j.msg || '读取失败';
        return;
      }
      document.getElementById('lpCode').value = j.text || '';
      document.getElementById('lpCodeMsg').textContent = (j.name || '') + ' · Ctrl+S 保存';
      lpCodeSync();
    }).catch(function (e) {
      document.getElementById('lpCodeMsg').textContent = '读取失败: ' + e.message;
    });
}
function lpCodeClose() {
  var m = document.getElementById('lpCodeModal');
  if (m) m.style.display = 'none';
}
function lpCodeReload() {
  if (_lpCodeRel) lpCodeOpen(_lpCodeRel, _lpCodeScope);
}
function lpCodeSave() {
  var ta = document.getElementById('lpCode');
  if (!ta) return;
  var msg = document.getElementById('lpCodeMsg');
  msg.textContent = '保存中…';
  fetch('/api/models/local/text', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rel: _lpCodeRel, scope: _lpCodeScope, text: ta.value })
  }).then(function (r) { return r.json(); }).then(function (j) {
    msg.textContent = j.ok ? '已保存' : ('保存失败: ' + (j.msg || ''));
    if (j.ok) notify('已保存 ' + (j.path || ''));
  }).catch(function (e) { msg.textContent = '保存失败: ' + e.message; });
}
(function () {
  document.addEventListener('dblclick', function (e) {
    var tr = e.target && e.target.closest ? e.target.closest('#lplTbody tr[data-rel]') : null;
    if (!tr || tr.getAttribute('data-kind') === 'folder') return;
    lpOnFileDblClick(tr.getAttribute('data-rel'), 'plugins');
  });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
      var m = document.getElementById('lpCodeModal');
      if (m && m.style.display !== 'none') { e.preventDefault(); lpCodeSave(); }
    }
    if (e.key === 'Escape') lpCodeClose();
  });
  var ta = document.getElementById('lpCode');
  if (ta) {
    ta.addEventListener('input', lpCodeSync);
    ta.addEventListener('scroll', function () {
      var no = document.getElementById('lpCodeNo');
      if (no) no.scrollTop = ta.scrollTop;
    });
  }
})();
