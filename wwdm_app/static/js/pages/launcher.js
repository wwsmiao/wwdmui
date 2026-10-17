
// ============================================================
// ComfyUI 启动器
// ============================================================
var cuiLogIdx=0,cuiAutoScroll=true,cuiPollTimer=null;
async function cuiCheckStatus(){
  try{var r=await fetch('/api/comfyui/status');var d=await r.json();
    var sb=document.getElementById('cuiStartBtn'),stb=document.getElementById('cuiStopBtn'),
        st=document.getElementById('cuiStatus');
    if(d.running){sb.disabled=true;stb.disabled=false;st.innerHTML='<span style="color:var(--green)">&#9673; 运行中</span> (PID '+d.pid+')';
      if(!cuiPollTimer){cuiPollTimer=setInterval(cuiPollLogs,1200);cuiPollLogs()}
    }else{sb.disabled=false;stb.disabled=true;
      if(cuiLogIdx>0){st.textContent='已停止'}else{st.textContent='准备就绪'}
      if(cuiPollTimer){clearInterval(cuiPollTimer);cuiPollTimer=null}}
  }catch(e){}
}
function cuiToggleAuto(){
  cuiAutoScroll=!cuiAutoScroll;
  var b=document.getElementById('cuiAutoBtn');
  if(!b)return;
  if(cuiAutoScroll){b.innerHTML='<i class="bi bi-arrow-down-circle"></i> 自动';b.title='自动滚动到最新日志';b.className='btn btn6 btn-sm'}
  else{b.innerHTML='<i class="bi bi-arrow-down-circle"></i> 手动';b.title='停止自动滚动，自由查看日志';b.className='btn btn-o btn-sm'}
  // 切换后立即跳一次
  if(cuiAutoScroll){var log=document.getElementById('cuiLog');if(log)log.scrollTop=log.scrollHeight}
}
function cuiCopyLog(){
  var log=document.getElementById('cuiLog');
  if(!log)return;
  var text=log.textContent||'';
  if(!text||text==='等待启动 ComfyUI ...'){notify('暂无日志可复制');return}
  var done=function(){notify('\u2705 已复制日志到剪贴板')};
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(done,function(){fallbackCopy(text,done)});
  }else{fallbackCopy(text,done)}
}
async function cuiStart(){
  var log=document.getElementById('cuiLog');log.textContent='正在启动 ComfyUI ...\n';
  cuiLogIdx=0;cuiAutoScroll=true;
  var b=document.getElementById('cuiAutoBtn');if(b){b.innerHTML='<i class="bi bi-arrow-down-circle"></i> 自动';b.className='btn btn6 btn-sm'}
  try{var r=await fetch('/api/comfyui/start',{method:'POST'});
    var d=await r.json();
    if(d.ok){cuiCmdPreview2();cuiPollLogs();cuiCheckStatus()}else{notify(d.msg)}
  }catch(e){notify('启动失败: '+e.message)}
}
async function cuiStop(){
  try{await fetch('/api/comfyui/stop',{method:'POST'});cuiCheckStatus()}catch(e){notify('停止失败: '+e.message)}
}
async function cuiPollLogs(){
  if(document.hidden)return;
  try{var r=await fetch('/api/comfyui/logs?since='+cuiLogIdx);var d=await r.json();
    if(d.lines&&d.lines.length>0){var log=document.getElementById('cuiLog');
      log.appendChild(document.createTextNode(d.lines.join('\n')+'\n'));
      if(log.textContent.length>500000){log.textContent=log.textContent.slice(-250000)}
      if(cuiAutoScroll)log.scrollTop=log.scrollHeight;cuiLogIdx=d.next_index}
  }catch(e){}
}
cuiCheckStatus();async function cuiCmdPreview2(){
  try{var r=await fetch('/api/comfyui/cmd_preview');var j=await r.json();
    var e=document.getElementById('cuiCmdPreview2');if(e)e.textContent='启动命令: '+j.cmd_str
  }catch(e){}}
cuiCmdPreview2();
