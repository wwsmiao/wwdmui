// 模型管理（CRUD + 下载）
// ============================================================
var mData=[],mPage=1,mPageSize=20,mTotalPages=1,mTotal=0;
var dlActiveIds=new Set(),dlQueuedIds=new Set(),dlSelIds=new Set(),dlProgTimer=null,dlLastStates={},dlPrevActive=new Set();

async function loadModels(){
  var q=document.getElementById('sinput').value.trim(),sort=document.getElementById('ssort').value;
  var p=new URLSearchParams();if(q)p.set('q',q);p.set('sort',sort);p.set('page',mPage);p.set('page_size',mPageSize);
  var r,ok=true;try{r=await fetch('/api/models?'+p.toString())}catch(e){ok=false}
  if(!ok||!r||!r.ok){document.getElementById('mtbody').innerHTML='<tr><td colspan="9" style="text-align:center;color:var(--red);padding:24px">加载失败</td></tr>';return}
  var j=await r.json();mData=j.items||[];mTotal=j.total||0;mPage=j.page||1;mTotalPages=j.total_pages||1;renderModels();updateMPagination()
}
function renderModels(){
  var tb=document.getElementById('mtbody');tb.innerHTML='';
  if(mData.length===0){tb.innerHTML='<tr><td colspan="9" style="text-align:center;color:var(--text3);padding:24px">暂无数据</td></tr>'}
  var _h=[];
  mData.forEach(function(m){
    var ch=dlSelIds.has(m.id)?'checked':'';
    var st=dlLastStates[m.id]||{},stat=st.status||'none';
    var sz=st.completed?(st.completed/1048576).toFixed(1)+'/'+(st.total?(st.total/1048576).toFixed(1):'?'):'-';
    var us=m.download_url.length>60?m.download_url.substring(0,57)+'...':m.download_url;
    var actions='<div style="display:flex;gap:3px;flex-wrap:wrap">'+
      '<button class="btn btn4 btn-xs" onclick="editModel('+m.id+')"><i class="bi bi-pencil"></i> 编辑</button>'+
            dlBtns(m.id,stat)+'</div>';
    _h.push('<tr id="dlr-'+m.id+'"><td class="cb-cell"><input type="checkbox" class="dlcb" value="'+m.id+'" '+ch+' onchange="dlOnSel('+m.id+',this.checked)"></td><td>'+m.id+'</td><td>'+esc(m.name)+'</td><td><code>'+esc(m.save_path)+'</code></td><td><a href="'+m.download_url+'" target="_blank" title="'+esc(m.download_url)+'">'+esc(us)+'</a></td><td id="dsz-'+m.id+'">'+sz+' MB</td><td id="dpg-'+m.id+'">'+renderMiniBar(st.progress||0)+'</td><td id="dst-'+m.id+'">'+dlBadge(stat)+'</td><td class="acts">'+actions+'</td></tr>')
  });
  if(_h.length)tb.innerHTML+=_h.join('');
  dlUpCnt();
  document.getElementById('mcount').textContent='共 '+mTotal+' 条'
}
function updateMPagination(){
  document.getElementById('minfo').textContent='第 '+mPage+' / '+mTotalPages+' 页';
  document.getElementById('mprev').disabled=mPage<=1;
  document.getElementById('mnext').disabled=mPage>=mTotalPages
}
async function loadPaths(){try{var r=await fetch('/api/paths');var p=await r.json();document.getElementById('plist').innerHTML=p.map(function(x){return '<option value="'+x+'">'}).join('')}catch(e){}}
function openModal(){document.getElementById('mtitle').textContent='新增模型';document.getElementById('eid').value='';document.getElementById('fname').value='';document.getElementById('fpath').value='';document.getElementById('furl').value='';document.getElementById('mbg').classList.add('show')}
async function editModel(id){try{var r=await fetch('/api/models/'+id);var m=await r.json();if(!m||!m.id)return;document.getElementById('mtitle').textContent='编辑 #'+id;document.getElementById('eid').value=id;document.getElementById('fname').value=m.name;document.getElementById('fpath').value=m.save_path;document.getElementById('furl').value=m.download_url;document.getElementById('mbg').classList.add('show')}catch(e){notify('加载失败')}}
function closeModal(){document.getElementById('mbg').classList.remove('show');loadModels()}
function resetModels(){
  document.getElementById('sinput').value='';
  document.getElementById('ssort').value='id_asc';
  mPage=1;loadModels();
}
// ====== 批量新增模型 ======
var _batItems=[];var BAT_EXTS=/\.(safetensors|ckpt|pt|pth|bin|onnx|gguf|sft|tensor|zip|tar\.gz|7z)/i;
function openBatModal(){
  _batItems=[];
  document.getElementById('batUrls').value='';
  document.getElementById('batPath').value='models/';
  document.getElementById('batPreview').innerHTML='等待输入链接...';
  document.getElementById('batMsg').textContent='';
  document.getElementById('batSubmitBtn').disabled=false;
  document.getElementById('batRegex').value='';
  document.getElementById('batmbg').classList.add('show');
  fetch('/api/paths').then(r=>r.json()).then(paths=>{
    var dl=document.querySelectorAll('#plist');
    if(dl.length>0){dl[0].innerHTML=paths.map(function(p){return '<option value="'+esc(p)+'">'}).join('');}
  }).catch(function(){});
}
function closeBatModal(){document.getElementById('batmbg').classList.remove('show')}
function _renderBatPreview(results){
  var prev=document.getElementById('batPreview');
  if(!results||results.length===0){prev.innerHTML='等待输入链接...';return}
  var html='<table style="width:100%;font-size:11.5px"><thead><tr style="color:var(--cyan)"><th style="text-align:left;padding:2px 4px">#</th><th style="text-align:left;padding:2px 4px">模型名称</th><th style="text-align:left;padding:2px 4px">下载链接</th></tr></thead><tbody>';
  results.forEach(function(item,i){
    var hasName=item.name&&!/^[\s?#]+$/.test(item.name);
    var rowStyle=hasName?'':'style="background:rgba(255,100,100,0.12)"';
    html+='<tr '+rowStyle+'><td style="padding:3px 4px;color:var(--text3)">'+(i+1)+'</td><td style="padding:3px 4px;color:'+(hasName?'var(--green)':'var(--red)')+';max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(item.name||'(未匹配)')+'">'+(hasName?esc(item.name):'<em>(未匹配)</em>')+'</td><td style="padding:3px 4px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(item.url)+'">'+esc(item.url)+'</td></tr>';
  });
  html+='</tbody></table>';
  prev.innerHTML=html;
  document.getElementById('batMsg').textContent='共 '+results.length+' 个模型待导入';
}
async function extractNames(){
  var raw=document.getElementById('batUrls').value.trim();
  if(!raw){notify('请输入下载链接');return}
  var regex=document.getElementById('batRegex').value.trim();
  var btn=document.getElementById('batExtractBtn');
  btn.disabled=true;btn.textContent='提取中...';
  try{
    var r=await fetch('/api/models/extract_names',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({urls:raw,regex:regex})
    });
    var j=await r.json();
    if(!j.ok){notify('提取失败: '+j.msg);return}
    _batItems=j.results;
    _renderBatPreview(j.results);
  }catch(e){
    notify('提取失败: '+e.message);
  }finally{
    btn.disabled=false;btn.textContent='提取';
  }
}
// 输入时本地实时预览（与服务端 /api/models/extract_names 同一套规则；正则语法非法就地提示）
// 注意：改这里必须同步改 routes.api_extract_names，两边规则要一致
var _batPvTimer=null;
function parseBatchUrls(){clearTimeout(_batPvTimer);_batPvTimer=setTimeout(_parseBatchUrlsNow,180)}
function _parseBatchUrlsNow(){
  var prev=document.getElementById('batPreview');if(!prev)return;
  var msgEl=document.getElementById('batMsg');
  _batItems=[];                       // 输入已变，之前「提取」的结果作废，防止提交过期数据
  var urls=(document.getElementById('batUrls').value||'').split(/\r?\n/).map(function(s){return s.trim()}).filter(Boolean);
  if(urls.length===0){prev.innerHTML='等待输入链接...';if(msgEl)msgEl.textContent='';return}
  var regex=(document.getElementById('batRegex').value||'').trim(),re=null;
  if(regex){
    try{re=new RegExp(regex,'i')}
    catch(e){
      prev.innerHTML='<span style="color:var(--red)">正则表达式错误: '+esc(e.message)+'</span>';
      if(msgEl)msgEl.textContent='';return;
    }
  }
  _renderBatPreview(urls.map(function(url){
    var name='';
    if(re){var m=re.exec(url);if(m){name=(m.length>1&&m[1]!=null)?m[1]:m[0]}}
    return {url:url,name:name};
  }));
  if(msgEl)msgEl.textContent='共 '+urls.length+' 个链接（预览），点「提取」确认';
}
document.addEventListener('DOMContentLoaded',function(){
  var ta=document.getElementById('batUrls');
  if(ta){ta.addEventListener('input',parseBatchUrls)}
  var br=document.getElementById('batRegex');
  if(br){br.addEventListener('input',parseBatchUrls)}
});
async function submitBatch(){
  if(!_batItems||_batItems.length===0){notify('请先点击「提取名称」');return}
  var items=_batItems;
  var savePath=document.getElementById('batPath').value.trim();
  if(!savePath){notify('请填写存放路径');return}
  if(items.length===0){notify('请至少输入一个下载链接');return}
  var btn=document.getElementById('batSubmitBtn');
  btn.disabled=true;btn.textContent='提交中...';
  document.getElementById('batMsg').textContent='正在批量导入...';
  try{
    var r=await fetch('/api/models/batch',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({save_path:savePath,items:items})
    });
    var j=await r.json();
    if(j.ok){
      document.getElementById('batMsg').textContent='\u2705 成功: '+j.added+' 个，跳过重复: '+j.skipped+' 个';
      loadModels();
      setTimeout(function(){closeBatModal()},1500);
    }else{
      document.getElementById('batMsg').textContent='\u274c '+j.msg;
      btn.disabled=false;btn.textContent='批量新增';
    }
  }catch(e){
    document.getElementById('batMsg').textContent='\u274c 请求失败: '+e.message;
    btn.disabled=false;btn.textContent='批量新增';
  }
}


async function saveModel(){
  var id=document.getElementById('eid').value,b={name:document.getElementById('fname').value.trim(),save_path:document.getElementById('fpath').value.trim(),download_url:document.getElementById('furl').value.trim()};
  if(!b.name||!b.save_path||!b.download_url){notify('请填写完整');return}
  try{if(id){await fetch('/api/models/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}else{var r=await fetch('/api/models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});var j=await r.json();if(!j.ok){notify(j.msg);return}}closeModal()}catch(e){notify('保存失败: '+e.message)}
}
async function delModel(id){if(!(await confirmDialog('确认删除 #'+id+'？',{danger:true})))return;try{await fetch('/api/models/'+id,{method:'DELETE'});loadModels()}catch(e){notify('删除失败')}}
