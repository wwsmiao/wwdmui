
// ============================================================
// ============================================================
// ============================================================
// ============================================================
// ============================================================
// ============================================================
// ============================================================
// 输入管理
// ============================================================
var inPath='',inData=null,inView='grid',inRendered=null,inItemMap={};
var _inZoom=1,_inPanX=0,_inPanY=0,_inDragging=false,_inDragSX=0,_inDragSY=0,_inDragPX=0,_inDragPY=0;
var _inState=null;

function inToggleSelectAll(){
  var cb=document.getElementById('inSelAll');
  var items=document.querySelectorAll('#inGrid [data-path]');
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
  inUpdateSelUI();
}

function inClearSelection(){
  _selPaths.clear();
  var all=document.querySelectorAll('#inGrid .selected');
  for(var i=0;i<all.length;i++)all[i].classList.remove('selected');
  var cb=document.getElementById('inSelAll');if(cb)cb.checked=false;
  inUpdateSelUI();
}

function inUpdateSelUI(){
  var cnt=_selPaths.size;
  var badge=document.getElementById('inSelCount');
  var clr=document.getElementById('inClearSel');
  var del=document.getElementById('inDelSel');
  if(cnt>0){
    badge.style.display='';badge.textContent='已选 '+cnt+' 项';
    clr.style.display='';del.style.display='';
  }else{
    badge.style.display='none';clr.style.display='none';del.style.display='none';
  }
}

async function inDeleteSelected(){
  if(_selPaths.size===0)return;
  if(!(await confirmDialog('确认删除 '+_selPaths.size+' 个文件/文件夹？\n此操作不可撤销！',{danger:true})))return;
  var paths=[];_selPaths.forEach(function(p){paths.push(p)});
  try{
    var r=await fetch('/api/input/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:paths})});
    var d=await r.json();
    _selPaths.clear();inUpdateSelUI();
    var cb=document.getElementById('inSelAll');if(cb)cb.checked=false;
    browseInput(inPath,inData?inData.page:1);
  }catch(e){notify('删除失败: '+e.message)}
}

async function browseInput(path,page){
  page=page||1;
  var newPath=path||'';
  if(newPath!==inPath){_selPaths.clear();var cb=document.getElementById('inSelAll');if(cb)cb.checked=false;inUpdateSelUI()}
  inPath=newPath;
  var ps=+document.getElementById('inPS').value||20;
  var sort=document.getElementById('inSort').value||'date_desc';
  var search=(document.getElementById('inSearch').value||'').trim();
  var q='path='+encodeURIComponent(inPath)+'&page='+page+'&page_size='+ps+'&sort='+sort;
  if(search)q+='&search='+encodeURIComponent(search);
  try{
    var r=await fetch('/api/input/browse?'+q);
    var d=await r.json();
    inData=d;
    if(!d.ok){notify(d.msg);return}
    inInitView();
    inItemMap={};d.items.forEach(function(it){inItemMap[it.path]=it});
    inRender(d,page);
  }catch(e){}
}

function inDoBrowse(){browseInput(inPath,1)}
function inSearch(){browseInput('',1)}
function inClearSearch(){
  document.getElementById('inSearch').value='';
  browseInput('',1);
}
function inOpenFolder(){
  fetch('/api/input/open_dir').then(function(r){return r.json()}).then(function(d){if(!d.ok)notify(d.msg)});
}
var _inViewTimer=null;
function inViewChange(){
  inView=document.getElementById('inViewSel').value||'grid';
  localStorage.setItem('inputViewMode',inView);
  clearTimeout(_inViewTimer);
  _inViewTimer=setTimeout(function(){inApplyView()},50);
}
function inInitView(){
  var v=localStorage.getItem('inputViewMode')||'grid';
  inView=v;
  var sel=document.getElementById('inViewSel');
  if(sel)sel.value=v;
}
function inApplyView(){
  var g=document.getElementById('inGrid');
  if(!g||!inData)return;
  var wasList=inRendered==='list',isList=inView==='list';
  if(wasList!==isList){inRender(inData,inData.page);return}
  if(!isList){
    var cls=inView==='grid'?'out-grid-regular':'out-grid-masonry';
    g.className=cls;g.style.display=inView==='grid'?'grid':'block';
    if(inView==='grid')g.style.gridTemplateColumns='repeat(4,1fr)';
    inRendered=inView;
  }
}

function inRender(d,pg){
  var bc=document.getElementById('inBread');
  var h='<a href="javascript:browseInput(\'\')" style="color:var(--cyan);text-decoration:none">&#128193;</a>';
  if(d.breadcrumbs)for(var i=0;i<d.breadcrumbs.length;i++){
    var b=d.breadcrumbs[i];
    h+=' / <a href="javascript:browseInput(\''+b.path.replace(/'/g,"\\'")+'\')" style="color:var(--cyan);text-decoration:none">'+escHtml(b.name)+'</a>';
  }
  bc.innerHTML=h;

  document.getElementById('inPageInfo').textContent=d.total>0?'共 '+d.total+' 项 | 第 '+d.page+'/'+d.total_pages+' 页':'无内容';

  var g=document.getElementById('inGrid');
  if(!d.items||!d.items.length){
    g.innerHTML='<div style="text-align:center;color:var(--text2);padding:40px">&#128194; 此目录下无可预览的媒体文件</div>';
    g.style.display='block';g.className='';inRendered='empty';
    inRenderPager(d);return;
  }

  requestAnimationFrame(function(){
    if(inView==='list'){
      g.style.display='block';g.className='';
      g.innerHTML='<table style="width:100%;border-collapse:collapse;color:var(--text)"><thead><tr style="border-bottom:1px solid var(--border)"><th style="text-align:left;padding:8px">名称</th><th style="width:80px;text-align:center;padding:8px">类型</th><th style="width:100px;text-align:right;padding:8px">大小</th><th style="width:140px;text-align:right;padding:8px">修改时间</th></tr></thead><tbody>'+d.items.map(inListRow).join('')+'</tbody></table>';
      inRendered='list';
    }else if(inView==='grid'){
      g.style.display='grid';g.style.gridTemplateColumns='repeat(4,1fr)';
      g.className='out-grid-regular';
      g.innerHTML=d.items.map(inGridItem).join('');
      inRendered='grid';
    }else{
      g.style.display='block';g.className='out-grid-masonry';
      g.innerHTML=d.items.map(inGridItem).join('');
      inRendered='masonry';
    }
    inRenderPager(d);
    if(_selPaths.size>0)inRestoreSelection();
  });
}

function inRestoreSelection(){
  var items=document.querySelectorAll('#inGrid [data-path]');
  var found=0;
  for(var i=0;i<items.length;i++){
    var p=items[i].getAttribute('data-path');
    if(_selPaths.has(p)){
      found++;
      items[i].classList.add(items[i].tagName==='TR'?'out-row':'out-item','selected');
    }
  }
  var cb=document.getElementById('inSelAll');
  if(cb)cb.checked=(found===items.length&&found>0);
  inUpdateSelUI();
}

function inGridItem(it){
  var icon=outIcon(it),nm=escHtml(it.name);
  var disp=nm.length>20?nm.substring(0,18)+'...':nm;
  var src='/api/input/file?path='+encodeURIComponent(it.path);
  var err=' onerror="this.style.display=\'none\';this.parentElement.innerHTML=\'<div class=out-fb style=font-size:48px>'+icon.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</div>\'"';
  var dpA=escHtml(it.path);
  var dpJ=_joinPath(it.path);
  var isd=it.is_dir?'1':'0';
  if(it.is_dir)
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="1" onclick="browseInput(\''+dpJ+'\')"><div class="out-thumb"><span style="font-size:48px">'+icon+'</span></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  if(it.type==='image')
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="inOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><img src="'+src+'" loading="lazy"'+err+'></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  if(it.type==='video')
    return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="inOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><video src="'+src+'" preload="metadata" muted'+err+'></video></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
  return '<div class="out-item" data-path="'+dpA+'" data-is-dir="0" onclick="inOpenFile(\''+dpJ+'\')"><div class="out-thumb is-file"><span style="font-size:48px">'+icon+'</span></div><div class="out-name" title="'+nm+'">'+disp+'</div></div>';
}

function inListRow(it){
  var icon=outIcon(it),nm=escHtml(it.name);
  var type=it.is_dir?'文件夹':{image:'图片',video:'视频',audio:'音频'}[it.type]||it.type;
  var sz=it.size?outFmtSize(it.size):'';var mt=it.modified?outFmtTime(it.modified):'';
  var dpA=escHtml(it.path);
  var dpJ=_joinPath(it.path);
  var isd=it.is_dir?'1':'0';
  if(it.is_dir)
    return '<tr data-path="'+dpA+'" data-is-dir="1" style="border-bottom:1px solid var(--border)"><td style="padding:8px"><a onclick="browseInput(\''+dpJ+'\')" style="cursor:pointer;color:var(--cyan)">'+icon+' '+nm+'</a></td><td style="text-align:center;padding:8px;color:var(--text2)">'+escHtml(type)+'</td><td style="text-align:right;padding:8px;color:var(--text2)">'+sz+'</td><td style="text-align:right;padding:8px;color:var(--text2);font-size:12px">'+mt+'</td></tr>';
  return '<tr data-path="'+dpA+'" data-is-dir="0" style="border-bottom:1px solid var(--border)"><td style="padding:8px"><a onclick="inOpenFile(\''+dpJ+'\')" style="cursor:pointer;color:var(--text)">'+icon+' '+nm+'</a></td><td style="text-align:center;padding:8px;color:var(--text2)">'+escHtml(type)+'</td><td style="text-align:right;padding:8px;color:var(--text2)">'+sz+'</td><td style="text-align:right;padding:8px;color:var(--text2);font-size:12px">'+mt+'</td></tr>';
}

function _joinPath(p){return p.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}

function inRenderPager(d){
  var pgEl=document.getElementById('inPager');
  if(!pgEl)return;
  if(d.total_pages<=1){pgEl.innerHTML='';return}
  var h='<button class="btn btn6" style="font-size:12px;padding:4px 8px"'+(d.page<=1?' disabled':' onclick="browseInput(inPath,'+(d.page-1)+')"')+'>'+'<i class="bi bi-chevron-left"></i> 上一页</button>';
  for(var i=Math.max(1,d.page-2),e=Math.min(d.total_pages,d.page+2);i<=e;i++)
    h+='<button class="btn '+(i===d.page?'btn1':'btn6')+'" style="font-size:12px;padding:4px 8px;min-width:32px" onclick="browseInput(inPath,'+i+')">'+i+'</button>';
  h+='<button class="btn btn6" style="font-size:12px;padding:4px 8px"'+(d.page>=d.total_pages?' disabled':' onclick="browseInput(inPath,'+(d.page+1)+')"')+'>'+'<i class="bi bi-chevron-right"></i> 下一页</button>';
  pgEl.innerHTML=h;
}

// Precisa preview overlay (simplified - reuse _out* functions via adapter)
function inOpenFile(path){
  var item=inItemMap[path],info=item||{};
  var src='/api/input/file?path='+encodeURIComponent(path);
  var fn=decodeURIComponent(path.split('/').pop()||path);
  var type=info.type||'image';

  var browseList=[];
  if(inData&&inData.items)inData.items.forEach(function(it){if(!it.is_dir)browseList.push(it)});
  var curIdx=-1;
  browseList.forEach(function(it,i){if(it.path===path)curIdx=i});

  inPath=_saveCur_ob=inPath;
  _saveCur_p=inPath; _saveCur_d=inData; _saveCur_b=browseInput;

  _outZoom=1;_outPanX=0;_outPanY=0;_outDragging=false;

  var existing=document.getElementById('outOverlay');
  if(existing){_outUpdateContent(existing,{src:src,fn:fn,type:type,info:info,browseList:browseList,curIdx:curIdx});return}

  var overlay=document.createElement('div');
  overlay.className='out-overlay';overlay.id='outOverlay';
  overlay.onclick=function(e){if(e.target===overlay)_outClose()};
  _outBuildOverlay(overlay,{src:src,fn:fn,type:type,info:info,browseList:browseList,curIdx:curIdx});
  document.body.appendChild(overlay);

  var onKey=function(e){
    if(e.key==='ArrowLeft'){e.preventDefault();_outNavigate(-1)}
    if(e.key==='ArrowRight'){e.preventDefault();_outNavigate(1)}
    if(e.key==='Escape')_outClose()
  };
  document.addEventListener('keydown',onKey);
  overlay._onKey=onKey;
}

// Ctrl+Click for input grid
(function(){
  var grid=document.getElementById('inGrid');
  if(grid)grid.addEventListener('click',function(e){
    var el=e.target.closest('[data-path]');
    if(!el)return;
    var path=el.getAttribute('data-path');
    if(e.ctrlKey){
      e.preventDefault();e.stopPropagation();
      if(_selPaths.has(path)){
        _selPaths.delete(path);
        el.classList.remove('selected');
      }else{
        _selPaths.add(path);
        el.classList.add(el.tagName==='TR'?'out-row':'out-item','selected');
      }
      var items=document.querySelectorAll('#inGrid [data-path]');
      var cb=document.getElementById('inSelAll');if(cb)cb.checked=(_selPaths.size===items.length&&_selPaths.size>0);
      inUpdateSelUI();
    }
  },true);
})();

// Contextmenu for input page - reuse shared ctx functions via curPage()
(function(){
  var box=document.getElementById('page-input');
  if(!box)return;
  box.addEventListener('contextmenu',function(e){
    if(!e.target.closest)return;
    if(e.target.closest('input, select, textarea, button, a, label'))return;
    e.preventDefault();
    window._curPage='input';
    window._curPath=inPath; window._curData=inData; window._curBrowse=browseInput;
    var el=e.target.closest('[data-path]');
    if(el)showCtxMenu(el,e.clientX,e.clientY,el.getAttribute('data-path'),el.getAttribute('data-is-dir'));
    else showCtxMenu(null,e.clientX,e.clientY,'',false);
  });
})();
