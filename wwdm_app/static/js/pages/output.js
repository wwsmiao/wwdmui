



// ============================================================

// ============================================================

// ============================================================
// ============================================================
// ============================================================
// 输出管理
// ============================================================
var outPath='',outData=null,outView='grid',outRendered=null,outItemMap={};
var _outState=null,_outZoom=1,_outPanX=0,_outPanY=0,_outDragging=false,_outDragSX=0,_outDragSY=0,_outDragPX=0,_outDragPY=0;
var _clipboard={action:'',paths:[]};
var _selPaths=new Set();

function outToggleSelectAll(){
  var cb=document.getElementById('outSelAll');
  var items=document.querySelectorAll('#outGrid [data-path]');
  if(cb.checked){
    for(var i=0;i<items.length;i++){
      var p=items[i].getAttribute('data-path');
      _selPaths.add(p);
      items[i].classList.add(items[i].tagName==='TR'?'out-row':'out-item','selected');
    }
  }else{
    for(var i=0;i<items.length;i++){
      _selPaths.delete(items[i].getAttribute('data-path'));
      items[i].classList.remove('selected');
    }
  }
  outUpdateSelUI();
}

function outClearSelection(){
  _selPaths.clear();
  var all=document.querySelectorAll('#outGrid .selected');
  for(var i=0;i<all.length;i++)all[i].classList.remove('selected');
  var cb=document.getElementById('outSelAll');if(cb)cb.checked=false;
  outUpdateSelUI();
}

function outUpdateSelUI(){
  var cnt=_selPaths.size;
  var badge=document.getElementById('outSelCount');
  var clr=document.getElementById('outClearSel');
  var del=document.getElementById('outDelSel');
  if(cnt>0){
    badge.style.display='';badge.textContent='已选 '+cnt+' 项';
    clr.style.display='';del.style.display='';
  }else{
    badge.style.display='none';clr.style.display='none';del.style.display='none';
  }
}

async function outDeleteSelected(){
  if(_selPaths.size===0)return;
  if(!(await confirmDialog('确认删除 '+_selPaths.size+' 个文件/文件夹？\n此操作不可撤销！',{danger:true})))return;
  var paths=[];_selPaths.forEach(function(p){paths.push(p)});
  try{
    var r=await fetch('/api/output/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:paths})});
    var d=await r.json();
    _selPaths.clear();outUpdateSelUI();
    var cb=document.getElementById('outSelAll');if(cb)cb.checked=false;
    browseOutput(outPath,outData?outData.page:1);
  }catch(e){notify('删除失败: '+e.message)}
}

function escHtml(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}

async function browseOutput(path,page){
  page=page||1;
  var newPath=path||'';
  if(newPath!==outPath){_selPaths.clear();var cb=document.getElementById('outSelAll');if(cb)cb.checked=false;outUpdateSelUI()}
  outPath=newPath;
  var ps=+document.getElementById('outPS').value||20;
  var sort=document.getElementById('outSort').value||'date_desc';
  var search=(document.getElementById('outSearch').value||'').trim();
  var q='path='+encodeURIComponent(outPath)+'&page='+page+'&page_size='+ps+'&sort='+sort;
  if(search)q+='&search='+encodeURIComponent(search);
  try{
    var r=await fetch('/api/output/browse?'+q);
    var d=await r.json();
    outData=d;
    if(!d.ok){notify(d.msg);return}
    outInitView();
    outItemMap={};d.items.forEach(function(it){outItemMap[it.path]=it});
    outRender(d,page);
  }catch(e){}
}

function outDoBrowse(){browseOutput(outPath,1)}
function outSearch(){browseOutput('',1)}
function outClearSearch(){
  document.getElementById('outSearch').value='';
  browseOutput('',1);
}
function outOpenFolder(){
  fetch('/api/output/open_dir').then(function(r){return r.json()}).then(function(d){if(!d.ok)notify(d.msg)});
}

var _viewTimer=null;
function outViewChange(){
  outView=document.getElementById('outViewSel').value||'grid';
  localStorage.setItem('outputViewMode',outView);
  clearTimeout(_viewTimer);
  _viewTimer=setTimeout(function(){outApplyView()},50);
}
function outInitView(){
  var v=localStorage.getItem('outputViewMode')||'grid';
  outView=v;
  var sel=document.getElementById('outViewSel');
  if(sel)sel.value=v;
}
function outApplyView(){
  var g=document.getElementById('outGrid');
  if(!g||!outData)return;
  var wasList=outRendered==='list',isList=outView==='list';
  if(wasList!==isList){outRender(outData,outData.page);return}
  if(!isList){
    var cls=outView==='grid'?'out-grid-regular':'out-grid-masonry';
    g.className=cls;g.style.display=outView==='grid'?'grid':'block';
    if(outView==='grid')g.style.gridTemplateColumns='repeat(4,1fr)';
    outRendered=outView;
  }
}

function outRender(d,pg){
  var bc=document.getElementById('outBread');
  var h='<a href="javascript:browseOutput(\'\')" style="color:var(--cyan);text-decoration:none">&#128193;</a>';
  if(d.breadcrumbs)for(var i=0;i<d.breadcrumbs.length;i++){
    var b=d.breadcrumbs[i];
    h+=' / <a href="javascript:browseOutput(\''+b.path.replace(/'/g,"\\'")+'\')" style="color:var(--cyan);text-decoration:none">'+escHtml(b.name)+'</a>';
  }
  bc.innerHTML=h;

  document.getElementById('outPageInfo').textContent=d.total>0?'共 '+d.total+' 项 | 第 '+d.page+'/'+d.total_pages+' 页':'无内容';

  var g=document.getElementById('outGrid');
  if(!d.items||!d.items.length){
    g.innerHTML='<div style="text-align:center;color:var(--text2);padding:40px">&#128194; 此目录下无可预览的媒体文件</div>';
    g.style.display='block';g.className='';outRendered='empty';
    outRenderPager(d);return;
  }

  requestAnimationFrame(function(){
    if(outView==='list'){
      g.style.display='block';g.className='';
      g.innerHTML='<table style="width:100%;border-collapse:collapse;color:var(--text)"><thead><tr style="border-bottom:1px solid var(--border)"><th style="text-align:left;padding:8px">名称</th><th style="width:80px;text-align:center;padding:8px">类型</th><th style="width:100px;text-align:right;padding:8px">大小</th><th style="width:140px;text-align:right;padding:8px">修改时间</th></tr></thead><tbody>'+d.items.map(outListRow).join('')+'</tbody></table>';
      outRendered='list';
    }else if(outView==='grid'){
      g.style.display='grid';g.style.gridTemplateColumns='repeat(4,1fr)';
      g.className='out-grid-regular';
      g.innerHTML=d.items.map(outGridItem).join('');
      outRendered='grid';
    }else{
      g.style.display='block';g.className='out-grid-masonry';
      g.innerHTML=d.items.map(outGridItem).join('');
      outRendered='masonry';
    }
    outRenderPager(d);
    if(_selPaths.size>0)outRestoreSelection();
  });
}

function outRestoreSelection(){
  var items=document.querySelectorAll('#outGrid [data-path]');
  var found=0;
  for(var i=0;i<items.length;i++){
    var p=items[i].getAttribute('data-path');
    if(_selPaths.has(p)){
      found++;
      items[i].classList.add(items[i].tagName==='TR'?'out-row':'out-item','selected');
    }
  }
  var cb=document.getElementById('outSelAll');
  if(cb)cb.checked=(found===items.length&&found>0);
  outUpdateSelUI();
}

function outGridItem(it){
  var icon=outIcon(it),nm=escHtml(it.name);
  var disp=nm.length>20?nm.substring(0,18)+'...':nm;
  var src='/api/output/file?path='+encodeURIComponent(it.path);
  var err=' onerror="this.style.display=\'none\';this.parentElement.innerHTML=\'<div class=out-fb style=font-size:48px>'+icon.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>\'"';
  var dpA=escHtml(it.path);
  var dpJ=outPathJoin(it.path);
  var isd=it.is_dir?'1':'0';
  if(it.is_dir)
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="1" onclick="browseOutput(\''+dpJ+'\')"><div class="out-thumb"><span style="font-size:48px">'+icon+'</span></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  if(it.type==='image')
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="outOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><img src="'+src+'" loading="lazy"'+err+'></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  if(it.type==='video')
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="outOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><video src="'+src+'" preload="metadata" muted'+err+'></video></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="outOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><span style="font-size:48px">'+icon+'</span></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
}

function outListRow(it){
  var icon=outIcon(it),nm=escHtml(it.name);
  var type=it.is_dir?'文件夹':{image:'图片',video:'视频',audio:'音频'}[it.type]||it.type;
  var sz=it.size?outFmtSize(it.size):'';var mt=it.modified?outFmtTime(it.modified):'';
  var dpA=escHtml(it.path);
  var dpJ=outPathJoin(it.path);
  var isd=it.is_dir?'1':'0';
  if(it.is_dir)
    return '<tr data-path="'+dpA+'" data-is-dir="1" style="border-bottom:1px solid var(--border)"><td style="padding:8px"><a onclick="browseOutput(\''+dpJ+'\')" style="cursor:pointer;color:var(--cyan)">'+icon+' '+nm+'</a></td><td style="text-align:center;padding:8px;color:var(--text2)">'+escHtml(type)+'</td><td style="text-align:right;padding:8px;color:var(--text2)">'+sz+'</td><td style="text-align:right;padding:8px;color:var(--text2);font-size:12px">'+mt+'</td></tr>';
  return '<tr data-path="'+dpA+'" data-is-dir="0" style="border-bottom:1px solid var(--border)"><td style="padding:8px"><a onclick="outOpenFile(\''+dpJ+'\')" style="cursor:pointer;color:var(--text)">'+icon+' '+nm+'</a></td><td style="text-align:center;padding:8px;color:var(--text2)">'+escHtml(type)+'</td><td style="text-align:right;padding:8px;color:var(--text2)">'+sz+'</td><td style="text-align:right;padding:8px;color:var(--text2);font-size:12px">'+mt+'</td></tr>';
}

function outIcon(it){if(it.is_dir)return'&#128193;';if(it.type==='video')return'&#127910;';if(it.type==='audio')return'&#127925;';return'&#128247;'}
function outPathJoin(p){return p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}
function outFmtSize(b){if(b<1024)return b+' B';if(b<1048576)return(b/1024).toFixed(1)+' KB';if(b<1073741824)return(b/1048576).toFixed(1)+' MB';return(b/1073741824).toFixed(1)+' GB'}
function outFmtTime(ts){var d=new Date(ts*1000);var pad=function(n){return n<10?'0'+n:n};return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes())}

function outRenderPager(d){
  var pgEl=document.getElementById('outPager');
  if(d.total_pages<=1){pgEl.innerHTML='';return}
  var h='<button class="btn btn6" style="font-size:12px;padding:4px 8px"'+(d.page<=1?' disabled':' onclick="browseOutput(outPath,'+(d.page-1)+')"')+'>'+'<i class="bi bi-chevron-left"></i> 上一页</button>';
  for(var i=Math.max(1,d.page-2),e=Math.min(d.total_pages,d.page+2);i<=e;i++)
    h+='<button class="btn '+(i===d.page?'btn1':'btn6')+'" style="font-size:12px;padding:4px 8px;min-width:32px" onclick="browseOutput(outPath,'+i+')">'+i+'</button>';
  h+='<button class="btn btn6" style="font-size:12px;padding:4px 8px"'+(d.page>=d.total_pages?' disabled':' onclick="browseOutput(outPath,'+(d.page+1)+')"')+'>'+'<i class="bi bi-chevron-right"></i> 下一页</button>';
  pgEl.innerHTML=h;
}
