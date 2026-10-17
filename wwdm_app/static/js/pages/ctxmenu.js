
// ============================================================
// 右键菜单
// ============================================================
var _ctxTarget=null;
function hideCtxMenu(){
  var m=document.getElementById('ctxMenu');if(m)m.style.display='none';
  _ctxTarget=null;
}
function showCtxMenu(el,x,y,path,isDir){
  var m=document.getElementById('ctxMenu');
  if(!m)return;
  _ctxTarget={el:el,path:path||'',is_dir:isDir==='1'||isDir===true,is_blank:!path};

  var copyEl=document.getElementById('ctxCopy'),moveEl=document.getElementById('ctxMove');
  var pasteEl=document.getElementById('ctxPaste'),s1=document.getElementById('ctxSep1'),s2=document.getElementById('ctxSep2');
  var delEl=document.getElementById('ctxDelete'),renEl=document.getElementById('ctxRename'),s3=document.getElementById('ctxSep3');
  var mkEl=document.getElementById('ctxNewFolder'),sendEl=document.getElementById('ctxSendToInput');
  var dlEl=document.getElementById('ctxDownload'),s0=document.getElementById('ctxSep0');
  var upFEl=document.getElementById('ctxUploadFile'),upDEl=document.getElementById('ctxUploadFolder');

  // 上传 / 新建的目标：右键命中文件夹 -> 进该文件夹；否则 -> 当前目录（与模型/插件本地页同一口径）
  var _ci=_ctxInfo();
  var _dest=_ctxTarget.is_dir?_ctxTarget.path:(_ci.path||'');
  var _nm=_dest?String(_dest).split('/').pop():'根目录';
  var _suffix=_dest?('「'+escHtml(_nm)+'」'):'根目录';
  var _isOut=(window._curPage!=='input'&&window._curPage!=='workflows');
  if(mkEl)mkEl.innerHTML='<i class="bi bi-folder-plus"></i> 新建文件夹';
  if(upFEl)upFEl.innerHTML='<i class="bi bi-upload"></i> 上传文件到 '+_suffix;
  if(upDEl)upDEl.innerHTML='<i class="bi bi-folder-symlink"></i> 上传文件夹到 '+_suffix;

  if(_ctxTarget.is_blank){
    if(copyEl)copyEl.style.display='none';
    if(moveEl)moveEl.style.display='none';
    if(delEl)delEl.style.display='none';
    if(renEl)renEl.style.display='none';
    if(dlEl)dlEl.style.display='none';
    if(s0)s0.style.display='';
    if(s1)s1.style.display='none';
    if(s3)s3.style.display='none';
    if(pasteEl)pasteEl.style.display=_clipboard.paths.length>0?'':'none';
    if(s2)s2.style.display=_clipboard.paths.length>0?'':'none';
    if(mkEl)mkEl.style.display='';
    if(upFEl)upFEl.style.display='';
    if(upDEl)upDEl.style.display='';
    if(sendEl)sendEl.style.display='none';
  }else{
    if(copyEl)copyEl.style.display='';
    if(moveEl)moveEl.style.display='';
    if(delEl)delEl.style.display='';
    if(renEl)renEl.style.display='';
    if(dlEl)dlEl.style.display='';
    if(s0)s0.style.display='';
    if(s1)s1.style.display='';
    if(s3)s3.style.display='';
    if(pasteEl)pasteEl.style.display=_clipboard.paths.length>0?'':'none';
    if(s2)s2.style.display=_clipboard.paths.length>0?'':'none';
    if(mkEl)mkEl.style.display='';
    if(upFEl)upFEl.style.display='';
    if(upDEl)upDEl.style.display='';
    if(sendEl)sendEl.style.display=_isOut?'':'none';
  }

  // 先显示再量尺寸：display:none 时 offsetHeight 恒为 0，
  // 菜单变高后（新增上传项）就会溢出到视口外看不见。
  m.style.display='block';
  m.style.left='0px';m.style.top='0px';
  var _mw=m.offsetWidth,_mh=m.offsetHeight;
  m.style.left=Math.max(6,Math.min(x,window.innerWidth-_mw-8))+'px';
  m.style.top=Math.max(6,Math.min(y,window.innerHeight-_mh-8))+'px';
}

function _ctxInfo(){
  if(window._curPage==='input')return{api:'/api/input',path:inPath,data:inData,browse:browseInput,delSel:inDeleteSelected,upUI:inUpdateSelUI};
  if(window._curPage==='workflows')return{api:'/api/workflows',path:wfState.curPath,data:null,browse:function(){wfDoBrowse()},delSel:wfDeleteSelected,upUI:function(){}};
  return{api:'/api/output',path:outPath,data:outData,browse:browseOutput,delSel:outDeleteSelected,upUI:outUpdateSelUI};
}

function ctxCopy(){
  if(!_ctxTarget||_ctxTarget.is_blank)return;
  var paths=[];
  if(_selPaths&&_selPaths.size>0){
    _selPaths.forEach(function(p){paths.push(p)});
  }else{
    paths.push(_ctxTarget.path);
  }
  _clipboard={action:'copy',paths:paths};
  hideCtxMenu();
}
function ctxMove(){
  if(!_ctxTarget||_ctxTarget.is_blank)return;
  var paths=[];
  if(_selPaths&&_selPaths.size>0){
    _selPaths.forEach(function(p){paths.push(p)});
  }else{
    paths.push(_ctxTarget.path);
  }
  _clipboard={action:'move',paths:paths};
  hideCtxMenu();
}
function ctxDownload(){
  if(!_ctxTarget||_ctxTarget.is_blank)return;
  var paths=[];
  if(_selPaths&&_selPaths.size>0){
    _selPaths.forEach(function(p){paths.push(p)});
  }else{
    paths.push(_ctxTarget.path);
  }
  var ci=_ctxInfo();
  var url=ci.api+'/download?paths='+encodeURIComponent(paths.join(','));
  hideCtxMenu();
  // 触发浏览器下载
  var a=document.createElement('a');
  a.href=url;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
async function ctxPaste(){
  if(!_ctxTarget||_clipboard.paths.length===0)return;
  var ci=_ctxInfo();
  var dest=_ctxTarget.is_dir?_ctxTarget.path:ci.path;
  var args=_clipboard.action==='move'?[ci.api+'/move',{paths:_clipboard.paths,dest:dest}]:
    [ci.api+'/copy',{paths:_clipboard.paths,dest:dest}];
  var r=await fetch(args[0],{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args[1])});
  var d=await r.json();
  if(d.ok||(d.results&&d.results.length)){
    var errs=d.results?d.results.filter(function(x){return!x.ok}):[];
    if(errs.length)notify('失败: '+errs.map(function(x){return x.path+': '+x.msg}).join('\n'));
    if(_clipboard.action==='move')_clipboard={action:'',paths:[]};
    ci.browse(ci.path,ci.data?ci.data.page:1);
  }else{notify(d.msg)}
  hideCtxMenu();
}
async function ctxDelete(){
  var ci=_ctxInfo();
  if(_selPaths.size>1){
    hideCtxMenu();
    ci.delSel();
    return;
  }
  if(!_ctxTarget||_ctxTarget.is_blank)return;
  var p=_ctxTarget.path;
  var name=p.split('/').pop();
  if(!(await confirmDialog('确认删除 "'+name+'"?\n此操作不可撤销！',{danger:true}))){hideCtxMenu();return}
  var r=await fetch(ci.api+'/delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({paths:[p]})});
  var d=await r.json();
  if(d.ok||d.results)ci.browse(ci.path,ci.data?ci.data.page:1);
  else notify(d.msg||'删除失败');
  hideCtxMenu();
}
async function ctxRename(){
  var ci=_ctxInfo();
  if(!_ctxTarget||_ctxTarget.is_blank)return;
  var p=_ctxTarget.path;
  var old=p.split('/').pop();
  var nm=await promptDialog('重命名:',old,{title:'重命名'});
  if(!nm||nm===old){hideCtxMenu();return;}
  hideCtxMenu();
  fetch(ci.api+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:p,new_name:nm})}).then(function(r){return r.json()}).then(function(d){
      if(d.ok)ci.browse(ci.path,ci.data?ci.data.page:1);
      else notify(d.msg||'重命名失败');
    });
}
async function ctxSendToInput(){
  // 优先使用多选路径，否则用右键点击的单个路径
  var paths = [];
  if(_selPaths && _selPaths.size > 0){
    _selPaths.forEach(function(p){paths.push(p)});
  }else if(_ctxTarget&&_ctxTarget.path){
    paths.push(_ctxTarget.path);
  }
  if(paths.length===0){hideCtxMenu();return}
  hideCtxMenu();
  try{
    var r=await fetch('/api/output/send_to_input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:paths})});
    var d=await r.json();
    if(d.ok){
      var cntOk=d.results.filter(function(x){return x.ok}).length;
      notify('\u2705 已发送 '+cntOk+'/'+paths.length+' 项到输入文件夹');
    }else{
      notify('\u274c 发送失败: '+(d.msg||''));
    }
  }catch(e){notify('\u274c 发送失败: '+e.message)}
}

async function ctxNewFolder(){
  var ci=_ctxInfo();
  var base=(_ctxTarget&&_ctxTarget.is_dir)?_ctxTarget.path:(ci.path||'');
  var where=base?('「'+String(base).split('/').pop()+'」'):'根目录';
  var nm=await promptDialog('在 '+where+' 下新建文件夹名称:','',{title:'新建文件夹'});
  hideCtxMenu();
  if(!nm)return;
  var p=base?base+'/'+nm:nm;
  fetch(ci.api+'/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:p})}).then(function(r){return r.json()}).then(function(d){
      if(d.ok){notify(d.msg||'目录已创建');ci.browse(ci.path,ci.data?ci.data.page:1)}
      else notify(d.msg||'创建失败');
    });
}

// ---- 右键上传（文件 / 文件夹，支持批量；文件夹会还原原目录结构）----
var _ctxUpFiles=null,_ctxUpDir=null,_ctxUpDest='',_ctxUpBrowse=null;

function _ctxUpToast(text){
  var t=notify(text,'info',1800000);
  return{
    set:function(s){var b=t&&t.querySelector('.ntf-body');if(b)b.textContent=s},
    close:function(){var x=t&&t.querySelector('.ntf-x');if(x)x.click()}
  };
}
function _ctxMkInput(dirMode){
  var i=document.createElement('input');
  i.type='file';i.multiple=true;i.style.display='none';
  i.id=dirMode?'ctxUpDirInput':'ctxUpFilesInput';
  if(dirMode){i.webkitdirectory=true;i.setAttribute('webkitdirectory','')}
  i.addEventListener('change',function(){
    // FileList 是活的：i.value='' 会把它一起清空，必须先拷成真数组
    var fs=i.files?Array.prototype.slice.call(i.files):[];
    var rels=[];
    if(dirMode){for(var k=0;k<fs.length;k++)rels.push(fs[k].webkitRelativePath||fs[k].name)}
    i.value='';
    if(fs.length)_ctxDoUpload(fs,rels);
  });
  document.body.appendChild(i);
  return i;
}
function _ctxPickUpload(dirMode){
  var ci=_ctxInfo();
  _ctxUpDest=(_ctxTarget&&_ctxTarget.is_dir)?_ctxTarget.path:(ci.path||'');
  _ctxUpBrowse=function(){try{ci.browse(ci.path,ci.data?ci.data.page:1)}catch(e){}};
  if(dirMode){if(!_ctxUpDir)_ctxUpDir=_ctxMkInput(true);_ctxUpDir.click();}
  else{if(!_ctxUpFiles)_ctxUpFiles=_ctxMkInput(false);_ctxUpFiles.click();}
}
function ctxUploadFile(){_ctxPickUpload(false)}
function ctxUploadFolder(){_ctxPickUpload(true)}
function _ctxDoUpload(files,rels){
  var ci=_ctxInfo();
  var n=files.length;
  var fd=new FormData();
  for(var i=0;i<n;i++)fd.append('files',files[i]);
  fd.append('dest',_ctxUpDest||'');
  for(var r=0;r<(rels||[]).length;r++)fd.append('paths',rels[r]);
  var t=_ctxUpToast('正在上传 '+n+' 个文件…');
  var x=new XMLHttpRequest();
  x.open('POST',ci.api+'/upload');
  x.upload.onprogress=function(e){
    if(e&&e.lengthComputable&&e.total)t.set('正在上传 '+n+' 个文件… '+Math.round(e.loaded*100/e.total)+'%');
  };
  x.onload=function(){
    t.close();
    var j=null;try{j=JSON.parse(x.responseText)}catch(err){}
    if(!j){notify('上传失败：服务端返回异常（HTTP '+x.status+'）');return}
    if(!j.ok){notify(j.msg||'上传失败');return}
    notify(j.msg||('已上传 '+n+' 个文件'));
    if(_ctxUpBrowse)_ctxUpBrowse();
  };
  x.onerror=function(){t.close();notify('上传失败：连接中断')};
  x.onabort=function(){t.close();notify('上传已取消')};
  x.send(fd);
}

// ---- Ctrl+Click 多选 ----
(function(){
  var grid=document.getElementById('outGrid');
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
      var items=document.querySelectorAll('#outGrid [data-path]');
      var cb=document.getElementById('outSelAll');if(cb)cb.checked=(_selPaths.size===items.length&&_selPaths.size>0);
      outUpdateSelUI();
    }
  },true);
})();

// 全局右键监听（整页生效；工具栏/表单控件保留原生菜单）
(function(){
  var box=document.getElementById('page-output');
  if(!box)return;
  box.addEventListener('contextmenu',function(e){
    if(!e.target.closest)return;
    if(e.target.closest('input, select, textarea, button, a, label'))return;
    e.preventDefault();
    window._curPage='output';
    var el=e.target.closest('[data-path]');
    if(el)showCtxMenu(el,e.clientX,e.clientY,el.getAttribute('data-path'),el.getAttribute('data-is-dir'));
    else showCtxMenu(null,e.clientX,e.clientY,'',false);
  });
})();

document.addEventListener('click',function(e){
  if(!e.target.closest('#ctxMenu'))hideCtxMenu();
});
