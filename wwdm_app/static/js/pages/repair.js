
// ============================================================
// 修复安装
// ============================================================
var rlogIdx=0,rlogTimer=null;

async function repairCheckDir(){
  var d=document.getElementById('rclone_dir').value.trim();
  var w=document.getElementById('rclone_warn');
  if(!d){w.style.display='none';return}
  try{var r=await fetch('/api/repair/clone_check?dir='+encodeURIComponent(d));
    var j=await r.json();
    if(j.exists){w.innerHTML='<span style="color:var(--green)">ComfyUI 已存在: '+d+'</span>';w.style.display='block'}
    else{w.style.display='none'}
  }catch(e){}
}

function repairLogPoll(){
  fetch('/api/repair/logs?since='+rlogIdx).then(function(r){return r.json()}).then(function(d){
    if(d.lines&&d.lines.length>0){
      var el=document.getElementById('rlog');
      d.lines.forEach(function(l){el.textContent+=l+'\n'});
      el.scrollTop=el.scrollHeight;rlogIdx=d.next_index
    }
    // check if finished
    fetch('/api/repair/status').then(function(r){return r.json()}).then(function(s){
      if(!s.running){
        clearInterval(rlogTimer);rlogTimer=null;
        setRlogStatus(s.action?'完成: '+s.action:'空闲');
        document.getElementById('rstop_btn').disabled=true;
        repairEnableBtns(true)
      }
    }).catch(function(){})
  }).catch(function(){})
}

function repairStartTimer(){
  rlogOpen();
  if(!rlogTimer){rlogTimer=setInterval(repairLogPoll,800)}
}

// ---- 运行日志大弹窗（仅修复安装页使用）----
function setRlogStatus(t){
  var a=document.getElementById('rlog_status'),b=document.getElementById('rlog_status2');
  if(a)a.textContent=t;
  if(b)b.textContent=t;
}
function rlogOpen(){
  var m=document.getElementById('rlogModal');
  if(m)m.style.display='flex';
}
function rlogClose(){
  var m=document.getElementById('rlogModal');
  if(m)m.style.display='none';
}
function rlogCopy(){
  var el=document.getElementById('rlog');
  if(!el)return;
  var text=el.textContent||'';
  if(!text.trim()||text.trim()==='等待操作...'){notify('暂无日志可复制');return}
  var done=function(){notify('\u2705 已复制完整日志到剪贴板')};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(done,function(){fallbackCopy(text,done)});
  }else{fallbackCopy(text,done)}
}
document.addEventListener('keydown',function(e){
  if(e.key!=='Escape')return;
  var m=document.getElementById('rlogModal');
  if(m&&m.style.display==='flex')rlogClose();
  var m2=document.getElementById('plLogModal');
  if(m2&&m2.style.display==='flex')plLogClose();
});

function repairEnableBtns(en){
  var ids=['rclone_btn','rreq_btn','rpt_btn','rpurge_btn','rcui_latest_btn','rcui_tag_btn','rpip_btn','rdl_btn','rplugin_btn','rall_btn','rkill_cui','rkill_a2'];
  ids.forEach(function(id){var e=document.getElementById(id);if(e)e.disabled=!en})
}

async function repairClone(){
  var dir=document.getElementById('rclone_dir').value.trim();
  if(!dir){notify('请填写安装目录');return}
  var log=document.getElementById('rlog');log.textContent='正在克隆 ComfyUI ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: 克隆');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/clone',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_dir:dir})});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairRequirements(){
  var log=document.getElementById('rlog');log.textContent='正在安装 requirements.txt ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: requirements');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/requirements',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairPytorch(){
  var cmd=document.getElementById('rpt_cmd').textContent.trim();
  if(!cmd||cmd==='等待选择...'){notify('请先选择 PyTorch 版本和计算平台');return}
  if(!cmd.startsWith('pip install')){notify('无效命令');return}
  var log=document.getElementById('rlog');log.textContent='正在安装 PyTorch ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: PyTorch');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/pytorch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd:cmd})});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairStop(){
  try{var r=await fetch('/api/repair/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();if(rlogTimer){clearInterval(rlogTimer);rlogTimer=null}
    setRlogStatus(j.ok?'已停止':'空闲');
    document.getElementById('rstop_btn').disabled=true;repairEnableBtns(true);
  }catch(e){}
}

async function repairPurgeCache(){
  if(!(await confirmDialog('确认清除 pip 缓存？这将删除所有已下载的 wheel 文件。',{danger:true})))return;
  var log=document.getElementById('rlog');log.textContent='正在清除 pip 缓存 ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: 清除缓存');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/purge_cache',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairLoadPytorch(){
  try{var r=await fetch('/api/repair/pytorch_options');rptData=await r.json();
    var vs=document.getElementById('rpt_ver');vs.innerHTML='';
    if(!rptData||rptData.length===0){vs.innerHTML='<option value="">无法加载 pytorch信息.txt</option>';return}
    rptData.forEach(function(o){vs.innerHTML+='<option value="'+o.version+'">torch v'+o.version+'</option>'});
    repairUpdateVariants()
  }catch(e){document.getElementById('rpt_ver').innerHTML='<option value="">加载失败</option>'}
}

var rptData=[];
function repairUpdateVariants(){
  var ver=document.getElementById('rpt_ver').value;
  var varsEl=document.getElementById('rpt_var');varsEl.innerHTML='';
  if(!ver){varsEl.innerHTML='<option value="">请先选择版本</option>';document.getElementById('rpt_cmd').textContent='等待选择...';return}
  var found=rptData.find(function(o){return o.version===ver});
  if(!found){varsEl.innerHTML='<option value="">无变体</option>';return}
  found.variants.forEach(function(v,i){varsEl.innerHTML+='<option value="'+i+'">'+v.label+'</option>'});
  repairUpdateCmd()
}

function repairUpdateCmd(){
  var ver=document.getElementById('rpt_ver').value;
  var idx=document.getElementById('rpt_var').value;
  var cmdEl=document.getElementById('rpt_cmd');
  if(!ver||idx===''||idx===null){cmdEl.textContent='等待选择...';return}
  var found=rptData.find(function(o){return o.version===ver});
  var v=found ? found.variants[parseInt(idx)] : null;
  cmdEl.textContent = v ? v.cmd : '未知变体';
}

// ---- 插件修复 ----
async function repairPipInstall(){
  var pkgs=document.getElementById('rpip_pkgs').value.trim();
  if(!pkgs){notify('请填写要安装的包名');return}
  var log=document.getElementById('rlog');log.textContent='正在安装: '+pkgs+' ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: pip install');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/pip_install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({packages:pkgs})});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairDownload(){
  var nm=document.getElementById('rdl_name').value.trim();
  var url=document.getElementById('rdl_url').value.trim();
  var sp=document.getElementById('rdl_path').value.trim();
  if(!nm){notify('请填写文件名称');return}
  if(!url){notify('请填写下载链接');return}
  if(!sp){notify('请填写保存路径');return}
  var log=document.getElementById('rlog');log.textContent='正在下载: '+nm+'\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: 下载文件');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:nm,url:url,save_path:sp})});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairPlugin(){
  var nm=document.getElementById('rplugin_name').value.trim();
  if(!nm){notify('请填写插件名称');return}
  var log=document.getElementById('rlog');log.textContent='正在修复插件: '+nm+'\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: 修复插件 '+nm);
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/plugin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plugin_name:nm})});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairAllPlugins(){
  if(!(await confirmDialog('确认批量修复所有插件？这将依次 git pull 并安装依赖，可能耗时较长。')))return;
  var log=document.getElementById('rlog');log.textContent='正在扫描 custom_nodes 并批量修复所有插件...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: 批量修复插件');
  repairEnableBtns(false);
  try{var r=await fetch('/api/repair/all_plugins',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

async function repairKillComfyUI(){
  if(!(await confirmDialog('确认强制结束所有 ComfyUI Python 进程？\n\n这将关闭所有正在运行的 ComfyUI 实例，可能导致未保存的工作丢失。',{danger:true,title:'强制结束'})))return;
  try{var r=await fetch('/api/repair/kill_comfyui',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();notify(j.msg)
  }catch(e){notify('失败: '+e.message)}
}

async function repairKillAria2(){
  if(!(await confirmDialog('确认强制结束所有 aria2c 进程？\n\n正在下载的任务将被中断。',{danger:true,title:'强制结束'})))return;
  try{var r=await fetch('/api/repair/kill_aria2',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    var j=await r.json();notify(j.msg)
  }catch(e){notify('失败: '+e.message)}
}



// ---- 更新 ComfyUI ----
var GH_MIRROR_LABELS={official:'GitHub 官方直连',gitclone:'gitclone.com 镜像',ur1:'github.ur1.fun 镜像',ghproxy:'gh-proxy.com 加速',ghproxynet:'ghproxy.net 加速',homeboyc:'ghproxy.homeboyc.cn 加速',akams:'github.akams.cn 加速'};
var PIP_MIRROR_LABELS={default:'国外官方 (pypi.org)',tsinghua:'清华大学 (tuna.tsinghua.edu.cn)',aliyun:'阿里云 (mirrors.aliyun.com)',tencent:'腾讯云 (mirrors.cloud.tencent.com)',douban:'豆瓣 (pypi.douban.com)',ustc:'中科大 (pypi.mirrors.ustc.edu.cn)'};
// URL 表以后端 /api/repair/mirror_info 的 pip_mirrors 为准，这里只作兜底
var PIP_MIRROR_URLS={default:'https://pypi.org/simple',tsinghua:'https://pypi.tuna.tsinghua.edu.cn/simple',aliyun:'https://mirrors.aliyun.com/pypi/simple/',tencent:'https://mirrors.cloud.tencent.com/pypi/simple',douban:'http://pypi.douban.com/simple/',ustc:'https://pypi.mirrors.ustc.edu.cn/simple'};
function _normMirrorUrl(u){return String(u==null?'':u).trim().toLowerCase().replace(/\/+$/,'')}

async function repairLoadMirrorInfo(){
  var elG=document.getElementById('rCuiGhMirror'),elP=document.getElementById('rCuiPipMirror'),elH=document.getElementById('rCuiUpdateHint');
  try{
    var r=await fetch('/api/repair/mirror_info');var j=await r.json();
    if(!j.ok)throw new Error(j.msg||'读取失败');
    var gl=GH_MIRROR_LABELS[j.github_mirror]||j.github_label||'GitHub 官方直连';
    var pl=j.pip_label||PIP_MIRROR_LABELS[j.pip_mirror]||j.pip_mirror||'官方源';
    var ambWarn='';
    if(j.pip_env_conflict){
      ambWarn=' <span style="color:var(--amber)">本机已有的 pip 配置指向 '+esc(j.pip_ambient_url||'')+'，实际执行时已被上面的设置覆盖</span>';
    }
    if(elG)elG.textContent=gl+(j.github_prefix?'':'（直连）');
    if(elP)elP.textContent=pl;
    if(elH){
      elH.innerHTML='本次更新将使用 — GitHub 镜像：<strong>'+esc(gl)+'</strong> ｜ pip 镜像源：<strong>'+esc(pl)+'</strong>（<code style="font-size:11px">'+esc(j.pip_url||'')+'</code>）；更新完会自动安装 requirements.txt 依赖。'
        +(j.github_prefix?'':' <span style="color:var(--amber)">当前为官方直连，国内可能较慢</span>')+ambWarn;
    }
    var spmN=document.getElementById('spmNote');
    if(spmN){
      if(!j.pip_ambient_url){
        spmN.textContent='未检测到系统 pip 镜像配置，pip 将直接访问所选镜像。';
      }else{
        // 与「下拉框当前选择」对比（可能是尚未保存的选择），而不是与已保存值对比
        var URLS=(j.pip_mirrors&&Object.keys(j.pip_mirrors).length)?j.pip_mirrors:PIP_MIRROR_URLS;
        var sel=(document.getElementById('spm')||{}).value||j.pip_mirror;
        var selUrl=URLS[sel]||j.pip_url||'';
        var same=_normMirrorUrl(selUrl)===_normMirrorUrl(j.pip_ambient_url);
        spmN.innerHTML='检测到系统 pip 配置：<code style="font-size:11px">'+esc(j.pip_ambient_url)+'</code>'
          +(same?' <span style="color:var(--green)">与当前选择一致</span>'
                :' <span style="color:var(--amber)">与当前选择不同 —— 实际执行时以本选择为准</span>');
      }
    }
    var gp=j.git_path||'git',pp=j.python_path||'python',ap=j.aria2c_path||'aria2c';
    var pu=(j.pip_url||''),puHtml=pu?('（<code style="font-size:11px">'+esc(pu)+'</code>）'):'';
    var notes=document.querySelectorAll('[data-mirror-note]');
    for(var ni=0;ni<notes.length;ni++){
      var nk=notes[ni].getAttribute('data-mirror-note'),nt='';
      if(nk==='git'){nt='git 路径：<strong>'+esc(gp)+'</strong> ｜ GitHub 镜像：<strong>'+esc(gl)+'</strong>';}
      else if(nk==='pip'){nt='pip 路径：<strong>'+esc(pp)+'</strong> ｜ pip 镜像源：<strong>'+esc(pl)+'</strong>'+puHtml+ambWarn;}
      else if(nk==='pytorch'){nt='pip 路径：<strong>'+esc(pp)+'</strong>（PyTorch 安装源由命令自带的 --index-url 决定）';}
      else if(nk==='aria2c'){nt='aria2c 路径：<strong>'+esc(ap)+'</strong>';}
      else if(nk==='both'){nt='git：<strong>'+esc(gp)+'</strong> ｜ GitHub 镜像：<strong>'+esc(gl)+'</strong> ｜ pip：<strong>'+esc(pp)+'</strong> ｜ pip 镜像源：<strong>'+esc(pl)+'</strong>'+puHtml+ambWarn;}
      notes[ni].innerHTML=nt;
    }
  }catch(e){
    if(elG)elG.textContent='读取失败';
    if(elP)elP.textContent='读取失败';
    if(elH)elH.textContent='镜像信息读取失败：'+e.message;
  }
}

var rCuiBranches=[];
var _rcuiGen=0;            // 代际号：重新加载 / 切页时 +1，作废在途轮询

// 把快照填进下拉框。busy=true 表示后台还在拉远端 ref，末尾加一条占位提示。
function _rcuiFill(sel,j,busy){
  var keep=sel.value;      // 轮询重绘时别打断用户已经选中的分支
  var html='<option value="">— 当前: '+esc(j.current||'unknown')+' —</option>';
  if(j.branches&&j.branches.length>0){
    html+='<optgroup label="远程分支">';
    j.branches.forEach(function(b){html+='<option value="'+esc(b)+'">'+esc(b)+'</option>'});
    html+='</optgroup>';
  }
  if(j.tags&&j.tags.length>0){
    html+='<optgroup label="标签 (tag)">';
    j.tags.forEach(function(t){html+='<option value="'+esc(t)+'">'+esc(t)+'</option>'});
    html+='</optgroup>';
  }
  if(busy) html+='<option value="" disabled>（正在后台拉取远端分支…）</option>';
  else if(j.error) html+='<option value="" disabled>（远端刷新未完成：'+esc(j.error)+'）</option>';
  sel.innerHTML=html;
  if(keep&&Array.prototype.some.call(sel.options,function(o){return o.value===keep})) sel.value=keep;
}

// 分支 / tag 列表：接口立刻返回本地 ref 快照，远端 git fetch 在后台线程里跑，
// 这里轮询到 refreshing=false 再定稿 —— 所以切到本页不会再出现「转很久的下拉框」。
async function repairLoadComfyUIBranches(){
  var sel=document.getElementById('rcui_branch');
  var gen=++_rcuiGen;
  if(!rCuiBranches.branches) sel.innerHTML='<option value="">加载中...</option>';
  try{
    var j=null;
    for(var i=0;i<30;i++){
      var r=await fetch('/api/repair/comfyui_branches');
      j=await r.json();
      if(gen!==_rcuiGen) return;
      if(!j.ok){sel.innerHTML='<option value="">'+esc(j.msg)+'</option>';return}
      if(!j.refreshing) break;
      rCuiBranches=j; _rcuiFill(sel,j,true);      // 先用本地快照顶上，不是空白等待
      await new Promise(function(g){setTimeout(g,1500)});
      if(gen!==_rcuiGen) return;
    }
    if(!j||!j.ok) return;
    rCuiBranches=j; _rcuiFill(sel,j,false);
  }catch(e){sel.innerHTML='<option value="">加载失败</option>'}
}
function repairCuiBranchChange(){
  var v=document.getElementById('rcui_branch').value;
  document.getElementById('rcui_tag_btn').disabled = (v==='');
}

async function repairComfyUIUpdate(target){
  var log=document.getElementById('rlog');
  var label = target ? '切换到 '+target : '更新到最新版 (git pull)';
  log.textContent='正在 '+label+' ...\n';
  rlogIdx=0;document.getElementById('rstop_btn').disabled=false;
  setRlogStatus('运行中: '+label);
  repairEnableBtns(false);
  try{
    var r=await fetch('/api/repair/comfyui_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:target})});
    var j=await r.json();
    if(!j.ok){notify(j.msg);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true;return}
    repairStartTimer()
  }catch(e){notify('失败: '+e.message);repairEnableBtns(true);document.getElementById('rstop_btn').disabled=true}
}

// ---- 恢复状态 ----
async function repairCheckStatus(){
  try{var r=await fetch('/api/repair/status');var s=await r.json();
    if(!s.running)return;
    setRlogStatus('运行中: '+s.action);
    document.getElementById('rstop_btn').disabled=false;
    repairEnableBtns(false);
    // reload logs
    r=await fetch('/api/repair/logs?since=0');var d=await r.json();
    document.getElementById('rlog').textContent=d.lines.join('\n');
    rlogIdx=d.next_index;repairStartTimer()
  }catch(e){}
}

// ============================================================
// 工作流管理（状态变量需在 INIT 之前声明，否则刷新恢复页面时报错）
// ============================================================
var wfState = { page: 1, pageSize: 20, totalPages: 1, curPath: '', sort: 'date_desc', search: '', editing: null };
var wfAnaModels = [];
var wfAnaPlugins = [];
var wfAnaPluginsAll = [];   // 解析结果的完整插件表（含已装），下载按钮按它取数
var _wfAnaPollTimer = null; // 下载进度轮询（保留：外部/测试会读）
var _wfAnaPolling = false;  // 是否已有一次轮询在途（防止链条重复叠加）
var _wfAnaPollGen = 0;      // 代际号：关窗 / 重新解析都 +1，作废所有在途结果
