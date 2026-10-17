
// ============================================================
// 插件克隆日志弹窗（仅插件管理页使用）
// ============================================================
function plLogOpen(){var m=document.getElementById('plLogModal');if(m)m.style.display='flex';plRenderMirrorLine()}
function plLogClose(){var m=document.getElementById('plLogModal');if(m)m.style.display='none'}
function plLogClear(){var el=document.getElementById('plClog');if(el)el.textContent='等待克隆任务 ...';plProgSeen={};plQueuedSeen={}}
function plLogStatus(t){var e=document.getElementById('plLogStatus');if(e)e.textContent=t}
function plLogAppend(line){var el=document.getElementById('plClog');if(!el)return;if(el.textContent==='等待克隆任务 ...')el.textContent='';var t=new Date();var ts=('0'+t.getHours()).slice(-2)+':'+('0'+t.getMinutes()).slice(-2)+':'+('0'+t.getSeconds()).slice(-2);el.textContent+='['+ts+'] '+line+'\n';el.scrollTop=el.scrollHeight}
async function plLoadMirror(){try{var r=await fetch('/api/repair/mirror_info');plMirrorInfo=await r.json()}catch(e){}plRenderMirrorLine()}
function plRenderMirrorLine(){
  var j=plMirrorInfo||{},lbl=j.github_label||'GitHub 官方';
  var el=document.getElementById('plMirrorLine');
  if(el)el.innerHTML='<i class="bi bi-globe2" style="color:var(--cyan)"></i> 克隆镜像源：<strong>'+esc(lbl)+'</strong>'+(j.github_prefix?('　<code style="font-size:11px">'+esc(j.github_prefix)+'</code>'):'　<span style="color:var(--text3)">（官方直连，国内可能较慢）</span>');
  var tg=document.getElementById('plMirrorTag');if(tg)tg.textContent=lbl;
}
function plLogBanner(){plRenderMirrorLine();var j=plMirrorInfo||{};plLogAppend('克隆镜像源：'+(j.github_label||'GitHub 官方')+(j.github_prefix?('  ('+j.github_prefix+')'):'  (官方直连)'))}
function plLogStart(id,msg){plLogOpen();plLogBanner();plLogAppend((plKnownNames[id]||('#'+id))+'  '+msg)}
function plLogSync(active,queue){
  (active||[]).forEach(function(s){
    plKnownNames[s.id]=s.name||plKnownNames[s.id]||('#'+s.id);
    var prev=plProgSeen[s.id]||{},pct=s.progress||0,ph=s.phase||'';
    var isNew=prev.status!==s.status,big=pct-(prev.pct===undefined?-100:prev.pct)>=10,phaseChanged=prev.phase!==ph;
    if(isNew||big||phaseChanged||pct>=100){
      plProgSeen[s.id]={status:s.status,phase:ph,pct:pct};
      var nm=plKnownNames[s.id];
      if(s.status==='cloning'){
        if(isNew)plLogAppend(nm+'  开始克隆'+(s.mirror?('（镜像：'+(s.mirror_label||'镜像')+'）'):'（官方直连）'));
        plLogAppend('    '+nm+'  '+(ph||'cloning')+'  '+pct+'%'+(s.msg?('  '+s.msg):''));
      }else if(s.status==='error'){plLogAppend('x '+nm+'  失败：'+(s.msg||''))}
    }
  });
  (queue||[]).forEach(function(q){
    plKnownNames[q.id]=q.name||plKnownNames[q.id]||('#'+q.id);
    if(plQueuedSeen[q.id]!==q.queue_position){plQueuedSeen[q.id]=q.queue_position;plLogAppend('… '+plKnownNames[q.id]+'  排队中（位置 '+q.queue_position+'）')}
  });
  var a=(active||[]).length,qn=(queue||[]).length;
  plLogStatus((a||qn)?((a?a+' 克隆中':'')+(a&&qn?' · ':'')+(qn?qn+' 排队中':'')):'空闲');
}

async function plStopAll(){
  var cnt=(plActiveIds.size)+(plQueuedIds.size);
  var ok=await confirmDialog('停止克隆将执行两步操作：\n\n1. 立即停止所有正在进行的克隆（含排队中的任务）\n2. 删除「克隆到一半」的插件文件夹\n   （已克隆完成的插件会保留，不会被删除）\n\n'+(cnt?('当前有 '+cnt+' 个任务在克隆/排队。'):'当前没有进行中的克隆任务。')+'\n是否继续？',{title:'停止克隆',okText:'停止并清理',danger:true,icon:'bi-stop-circle'});
  if(!ok)return;
  plLogOpen();
  plLogAppend('=== 停止克隆并清理目录 ===');
  try{
    var r=await fetch('/api/plugins/clone_stop_all',{method:'POST'});
    var j=await r.json();
    if(!j.ok){notify(j.msg||'操作失败');return}
    (j.stopped||[]).forEach(function(nm){plLogAppend('x 已停止克隆: '+nm)});
    (j.removed||[]).forEach(function(nm){plLogAppend('  已删除目录（未克隆完成）: '+nm)});
    (j.kept||[]).forEach(function(nm){plLogAppend('v 已保留（克隆完成）: '+nm)});
    (j.skipped||[]).forEach(function(nm){plLogAppend('  - 无目录可删: '+nm)});
    plLogStatus('空闲');
    notify(j.msg);
  }catch(e){notify('操作失败: '+e.message)}
  plActiveIds.clear();plQueuedIds.clear();plLastStates={};plPrevActive=new Set();plCancelledIds.clear();
  plProgSeen={};plQueuedSeen={};
  if(plProgTimer){clearInterval(plProgTimer);plProgTimer=null}
  renderPlugins();
}

// ---- 插件数据库修复：去重 + 序号整理为 1~n（操作前自动备份）----
async function repairPluginDb(){
  var ok=await confirmDialog('将对插件数据库执行以下操作：\n\n1. 备份数据库文件（可回滚）\n2. 去除重复插件（名称与仓库地址都相同的记录，保留最早一条）\n3. 把插件序号重新整理为连续的 1~n\n\n同名但不同仓库的插件属于不同插件，不会被合并。是否继续？',{title:'修复插件库',okText:'开始修复',icon:'bi-wrench-adjustable'});
  if(!ok)return;
  notify('正在修复插件库...');
  try{
    var r=await fetch('/api/plugins/repair_db',{method:'POST'});var j=await r.json();
    if(!j.ok){notify(j.msg||'修复失败');return}
    var d=j.result||{};
    var names=(d.removed||[]).map(function(x){return (x.name||('#'+x.id))+' (#'+x.id+')'});
    console.log('[插件库修复] 删除的重复项：',d.removed);
    notify('修复完成：共 '+d.total_before+' → '+d.total_after+' 条，去除重复 '+(d.removed_count||0)+' 个，序号已整理为 1-'+d.total_after+' 连续序列。\n已备份：'+(d.backup||'').split(/[\\/]/).pop());
    plLogBanner();plLogAppend('插件库修复：'+d.total_before+' → '+d.total_after+' 条，去除重复 '+(d.removed_count||0)+' 个，序号已整理为 1-'+d.total_after);
    names.slice(0,20).forEach(function(n){plLogAppend('  已删除重复: '+n)});
    plSelIds.clear();loadPlugins();
  }catch(e){notify('修复失败: '+e.message)}
}
var _plLibBusy=false;
async function updatePluginLibrary(){
  if(_plLibBusy){notify('正在更新中，请稍候…');return}
  var ok=await confirmDialog('将从 ComfyUI-Manager 的官方数据源下载最新插件库，合并写入本地数据库：\n\n• 仓库地址已存在的插件 → 覆盖名称与描述\n• 仓库地址没有的插件 → 新增\n• 不会产生重复记录，也不会删除你手动添加的条目\n• 同时刷新「节点 → 插件」映射表（工作流解析用）\n\n需要联网。是否继续？',{title:'更新插件库',okText:'开始更新',icon:'bi-cloud-arrow-down'});
  if(!ok)return;
_plLibBusy=true;
  notify('正在下载插件库，请稍候…');
  plLogBanner();plLogAppend('开始更新插件库（数据源：ComfyUI-Manager 官方）…');
  try{
    var r=await fetch('/api/plugins/update_library',{method:'POST'});
    var j=await r.json();
    if(!j.ok){
      notify(j.msg||'更新失败');
      (j.errors||[]).slice(0,6).forEach(function(e){plLogAppend('  x '+e)});
      return;
    }
    var nm=j.node_map||{};
    var line='插件库更新完成（'+(j.elapsed||0)+'s · 源：'+(j.source||'-')+'）：已有覆盖 '+(j.updated||0)+' · 新增 '+(j.added||0)+' · 未变 '+(j.unchanged||0)+' · 共 '+(j.total||0)+' 条';
    var line2='节点映射表：'+(nm.pairs||0)+' 条关联 · '+(nm.nodes||0)+' 个节点 · '+(nm.urls||0)+' 个插件';
    notify(line+'\n'+line2);
    plLogAppend(line);plLogAppend(line2);
    (j.errors||[]).slice(0,6).forEach(function(e){plLogAppend('  次要：'+e)});
    plPage=1;loadPlugins();
  }catch(e){notify('更新失败: '+e.message);plLogAppend('  更新失败: '+e.message)}
  finally{_plLibBusy=false}
}
function clUpdateCell(id,s){var sc=document.getElementById('plst-'+id);if(!sc)return;sc.innerHTML=clBadge(s.status)+(s.status==='cloning'?'<span style="font-size:10px;color:var(--text2);margin-left:4px">'+esc(s.phase)+' '+s.progress+'%</span>':'');var bc=document.getElementById('cbclone-'+id),bn=document.getElementById('cbcancel-'+id);if(!bc)return;if(s.status==='cloning'){bc.disabled=true;bc.textContent='克隆中';bn.disabled=false}else if(s.status==='complete'){bc.disabled=true;bc.textContent='已完成';bn.disabled=true}else if(s.status==='error'){bc.disabled=false;bc.textContent='克隆';bn.disabled=true}}
async function restoreCloneState(){
  try{var r=await fetch('/api/plugins/clone_all_status');var d=await r.json();plActiveIds.clear();plQueuedIds.clear();plLastStates={};plPrevActive=new Set();
    d.active.forEach(function(x){plActiveIds.add(x.id);plPrevActive.add(x.id);plKnownNames[x.id]=x.name||plKnownNames[x.id];plLastStates[x.id]={status:x.status,progress:x.progress||0,phase:x.phase,msg:x.msg,mirror:x.mirror}});
    d.queue.forEach(function(x){plQueuedIds.add(x.id);plKnownNames[x.id]=x.name||plKnownNames[x.id];plLastStates[x.id]={status:'queue',progress:0}});
    renderPlugins();plLogSync(d.active,d.queue);if(d.active.length>0||d.queue.length>0){plStartTimer();plLogOpen()}
  }catch(e){}
}
