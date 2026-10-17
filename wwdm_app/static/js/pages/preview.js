
// ============================================================
// 预览弹窗
// ============================================================
function outOpenFile(path){
  var item=outItemMap[path],info=item||{};
  var src='/api/output/file?path='+encodeURIComponent(path);
  var fn=decodeURIComponent(path.split('/').pop()||path);
  var type=info.type||'image';

  var browseList=[];
  if(outData&&outData.items)outData.items.forEach(function(it){if(!it.is_dir)browseList.push(it)});
  var curIdx=-1;
  browseList.forEach(function(it,i){if(it.path===path)curIdx=i});

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
    if(e.key==='Escape'){e.preventDefault();_outClose()}
  };
  document.addEventListener('keydown',onKey);overlay._keyHandler=onKey;
}

function _outClose(){
  var o=document.getElementById('outOverlay');
  if(o){if(o._keyHandler)document.removeEventListener('keydown',o._keyHandler);document.body.removeChild(o)}
  if(window._outVid){window._outVid.pause();window._outVid=null}
  _outState=null;try{if(document.fullscreenElement)document.exitFullscreen()}catch(e){}
}

function _outBuildOverlay(overlay,st){
  var arrows=st.browseList.length>1?
    '<button class="out-arrow out-arrow-l" onclick="_outNavigate(-1)" title="上一张"><i class="bi bi-chevron-left"></i></button>'+
    '<button class="out-arrow out-arrow-r" onclick="_outNavigate(1)" title="下一张"><i class="bi bi-chevron-right"></i></button>':'';

  var topBar='<div style="display:flex;justify-content:space-between;align-items:center;flex-shrink:0;margin-bottom:6px">'+
    '<span style="color:var(--text);font-size:14px;word-break:break-all;max-width:55%;margin:0 auto" id="outTitle">'+escHtml(st.fn)+'</span>'+
    '<div style="display:flex;gap:6px;flex-shrink:0">'+
    '<button class="btn btn6" onclick="outToggleFullscreen()" title="全屏" style="font-size:12px;padding:4px 10px"><i class="bi bi-arrows-fullscreen"></i> 全屏</button>'+
    '<button class="btn btn5" onclick="_outClose()" style="font-size:12px"><i class="bi bi-x-lg"></i> 关闭</button></div></div>';

  var content;
  if(st.type==='image'){
    content='<div class="out-fs-wrap" id="outFsWrap">'+
      '<img id="outFsImg" class="out-fs-nav-img" src="'+st.src+'" style="max-width:100%;max-height:100%;object-fit:contain;opacity:0;transition:opacity 0.2s" onload="outImgReady()">'+
      '</div>'+
      '<div id="outInfoBar" style="display:flex;align-items:center;gap:8px;margin-top:6px;padding:6px 10px;background:rgba(74,111,165,0.06);border-radius:10px;font-size:12px;color:var(--text2);flex-shrink:0;flex-wrap:wrap">'+
      '<span id="outInfoRes"></span>'+
      '<span>|</span><span>'+outFmtSize(st.info.size||0)+'</span>'+
      '<span>|</span><span>'+outFmtTime(st.info.modified||0)+'</span>'+
      '<span style="margin-left:auto;display:flex;align-items:center;gap:6px">'+
      '<button class="btn btn6" onclick="outZoomReset()" style="font-size:11px;padding:2px 8px" title="重置"><i class="bi bi-arrow-counterclockwise"></i> 重置</button>'+
      '<span style="margin-left:4px;opacity:0.5;font-size:10px" id="outTotalInfo">'+(st.curIdx+1)+'/'+st.browseList.length+'</span></span></div>';
  }else if(st.type==='video'){
    content='<div style="flex:1;display:flex;align-items:center;justify-content:center;min-height:0">'+
      '<video id="outVid" src="'+st.src+'" controls autoplay style="max-width:100%;max-height:100%" onplay="window._outVid=this" onpause="window._outVid=null"></video></div>'+
      '<div id="outInfoBar" style="display:flex;align-items:center;gap:10px;margin-top:6px;padding:6px 10px;background:rgba(74,111,165,0.06);border-radius:10px;font-size:12px;color:var(--text2);flex-shrink:0">'+
      '<span>'+outFmtSize(st.info.size||0)+' '+outFmtTime(st.info.modified||0)+'</span>'+
      '<span style="margin-left:auto;opacity:0.5;font-size:10px" id="outTotalInfo">'+(st.curIdx+1)+'/'+st.browseList.length+'</span></div>';
  }else{
    content='<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:0">'+
      '<audio id="outVid" src="'+st.src+'" controls autoplay style="max-width:100%" onplay="window._outVid=this" onpause="window._outVid=null"></audio>'+
      '<div style="font-size:64px;margin:16px">🎵</div></div>'+
      '<div id="outInfoBar" style="display:flex;align-items:center;gap:10px;margin-top:6px;padding:6px 10px;background:rgba(74,111,165,0.06);border-radius:10px;font-size:12px;color:var(--text2);flex-shrink:0">'+
      '<span>'+outFmtSize(st.info.size||0)+' '+outFmtTime(st.info.modified||0)+'</span></div>';
  }

  overlay.innerHTML=arrows+'<div class="out-modal">'+topBar+content+'</div>';
  _outState=st;
}

function _outUpdateContent(overlay,st){
  _outZoom=1;_outPanX=0;_outPanY=0;_outDragging=false;
  _outState=st;

  var t=document.getElementById('outTitle');if(t)t.textContent=st.fn;
  var ti=document.getElementById('outTotalInfo');if(ti&&st.browseList.length)ti.textContent=(st.curIdx+1)+'/'+st.browseList.length;

  var arrows=overlay.querySelectorAll('.out-arrow');
  arrows.forEach(function(a){a.style.display=st.browseList.length>1?'':'none'});

  if(st.type==='image'){
    var img=document.getElementById('outFsImg');
    if(img){
      img.style.opacity='0';
      img.src=st.src;
      _applyZoom();
      var bar=document.getElementById('outInfoBar');
      if(bar)bar.innerHTML='<span id="outInfoRes"></span>'+
        '<span>|</span><span>'+outFmtSize(st.info.size||0)+'</span>'+
        '<span>|</span><span>'+outFmtTime(st.info.modified||0)+'</span>'+
        '<span style="margin-left:auto;display:flex;align-items:center;gap:6px">'+
        '<button class="btn btn6" onclick="outZoomReset()" style="font-size:11px;padding:2px 8px" title="重置"><i class="bi bi-arrow-counterclockwise"></i> 重置</button>'+
        '<span style="margin-left:4px;opacity:0.5;font-size:10px">'+(st.curIdx+1)+'/'+st.browseList.length+'</span></span>';
    }
  }else if(st.type==='video'){
    var modal=overlay.querySelector('.out-modal');
    if(modal){
      while(modal.children.length>1)modal.removeChild(modal.lastChild);
      var nc='<div style="flex:1;display:flex;align-items:center;justify-content:center;min-height:0">'+
        '<video id="outVid" src="'+st.src+'" controls autoplay style="max-width:100%;max-height:100%" onplay="window._outVid=this" onpause="window._outVid=null"></video></div>'+
        '<div id="outInfoBar" style="display:flex;align-items:center;gap:10px;margin-top:6px;padding:6px 10px;background:rgba(74,111,165,0.06);border-radius:10px;font-size:12px;color:var(--text2);flex-shrink:0">'+
        '<span>'+outFmtSize(st.info.size||0)+' '+outFmtTime(st.info.modified||0)+'</span>'+
        '<span style="margin-left:auto;opacity:0.5;font-size:10px">'+(st.curIdx+1)+'/'+st.browseList.length+'</span></div>';
      var tmpDiv=document.createElement('div');tmpDiv.innerHTML=nc;
      while(tmpDiv.firstChild)modal.appendChild(tmpDiv.firstChild);
    }
  }
}

function _outNavigate(dir){
  if(!_outState||_outState.browseList.length<2)return;
  _outState.curIdx=(_outState.curIdx+dir+_outState.browseList.length)%_outState.browseList.length;
  var it=_outState.browseList[_outState.curIdx];
  var apiPrefix=_outState.src.split('/file?')[0];
  _outUpdateContent(document.getElementById('outOverlay'),{
    src:apiPrefix+'/file?path='+encodeURIComponent(it.path),
    fn:decodeURIComponent(it.path.split('/').pop()||it.path),
    type:it.type||'image',info:it,
    browseList:_outState.browseList,curIdx:_outState.curIdx
  });
}

function outImgReady(){
  var img=document.getElementById('outFsImg');
  if(!img)return;
  var res=document.getElementById('outInfoRes');
  if(res)res.textContent=(img.naturalWidth||'?')+' × '+(img.naturalHeight||'?')+' px';
  img.style.opacity='1';
  _outZoom=1;_outPanX=0;_outPanY=0;
}

function outToggleFullscreen(){
  var o=document.getElementById('outOverlay');
  if(!o)return;
  if(document.fullscreenElement||document.webkitFullscreenElement){
    try{document.exitFullscreen()}catch(e){}
  }else{
    try{o.requestFullscreen?o.requestFullscreen():o.webkitRequestFullscreen()}catch(e){}
  }
}

function _onFsChange(){
  _outZoom=1;
  var img=document.getElementById('outFsImg');
  var wrap=document.getElementById('outFsWrap');
  if(img&&wrap){
    var wr=wrap.getBoundingClientRect();
    _outPanX=(wr.width-img.naturalWidth)/2;
    _outPanY=(wr.height-img.naturalHeight)/2;
  }else{
    _outPanX=0;_outPanY=0;
  }
  _applyZoom();
  var bar=document.getElementById('outInfoBar');
  if(bar)bar.style.display=document.fullscreenElement||document.webkitFullscreenElement?'none':'';
}
document.addEventListener('fullscreenchange',_onFsChange);
document.addEventListener('webkitfullscreenchange',_onFsChange);

function _applyZoom(){
  var img=document.getElementById('outFsImg');
  if(!img)return;
  var wrap=document.getElementById('outFsWrap');
  if(_outZoom <= 1){
    // 未缩放：用 CSS contain 显示完整图片，flexbox 居中
    if(wrap){wrap.style.justifyContent='center';wrap.style.alignItems='center';}
    img.style.maxWidth='';
    img.style.maxHeight='';
    img.style.objectFit='';
    img.style.transform='';
  }else{
    // 缩放模式：flexbox 对齐左上角，transform 平移图片
    if(wrap){wrap.style.justifyContent='flex-start';wrap.style.alignItems='flex-start';}
    img.style.maxWidth='none';
    img.style.maxHeight='none';
    img.style.objectFit='none';
    img.style.transformOrigin='0 0';
    img.style.transform='translate('+_outPanX+'px,'+_outPanY+'px) scale('+_outZoom+')';
  }
}

function outZoomReset(){
  _outZoom=1;_outPanX=0;_outPanY=0;
  var wrap=document.getElementById('outFsWrap');
  _applyZoom();
  if(wrap)wrap.style.cursor='grab';
}

// 滚轮缩放 + 左键拖动
(function(){
  document.addEventListener('wheel',function(e){
    var wrap=document.getElementById('outFsWrap'),img=document.getElementById('outFsImg');
    if(!wrap||!img||_outDragging)return;
    var r=wrap.getBoundingClientRect();
    if(e.clientX<r.left||e.clientY<r.top||e.clientX>r.right||e.clientY>r.bottom)return;
    e.preventDefault();
    var old=_outZoom;
    _outZoom=Math.max(0.3,Math.min(10,_outZoom-(e.deltaY>0?0.15:-0.15)));
    var mx=e.clientX-r.left,my=e.clientY-r.top;
    // 从 CSS contain 进入 transform 模式：用 contain 的等效缩放作为 old
    if(old<=1&&_outZoom>1){
      var nw=img.naturalWidth,nh=img.naturalHeight;
      var s=Math.min(r.width/nw,r.height/nh);
      _outPanX=(r.width-nw*s)/2;
      _outPanY=(r.height-nh*s)/2;
      old=s;
    }
    var rt=_outZoom/old;
    _outPanX=mx-rt*(mx-_outPanX);
    _outPanY=my-rt*(my-_outPanY);
    _applyZoom();
  },{passive:false});

  document.addEventListener('mousedown',function(e){
    if(e.button!==0)return;
    var wrap=document.getElementById('outFsWrap');
    if(!wrap)return;
    var r=wrap.getBoundingClientRect();
    if(e.clientX<r.left||e.clientY<r.top||e.clientX>r.right||e.clientY>r.bottom)return;
    e.preventDefault();e.stopPropagation();
    _outDragging=true;_outDragSX=e.clientX;_outDragSY=e.clientY;
    _outDragPX=_outPanX;_outDragPY=_outPanY;
    wrap.style.cursor='grabbing';
  });

  document.addEventListener('mousemove',function(e){
    if(!_outDragging)return;
    _outPanX=_outDragPX+(e.clientX-_outDragSX)/_outZoom;
    _outPanY=_outDragPY+(e.clientY-_outDragSY)/_outZoom;
    _applyZoom();
  });

  document.addEventListener('mouseup',function(){
    if(!_outDragging)return;
    _outDragging=false;
    var wrap=document.getElementById('outFsWrap');if(wrap)wrap.style.cursor='grab';
  });
})();
