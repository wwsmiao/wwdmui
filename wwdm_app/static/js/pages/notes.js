
// ============================================================
// 笔记本
// ============================================================
// v5.1：从侧边栏页面改为右下角 FAB 唤出的大弹窗（templates/partials/notes.html）。
// switchPage 不再触发 notesInit()，入口统一走 notesOpen()。
var noteList=[],noteCurId=null,noteTags=[],noteDebounceTimer=null;
var notePreviewMode=false;

function notesInit(){
    noteCurId=null;
    notePreviewMode=false;
    noteLoadAll();
}

// ---- 弹窗开合（v5.1）----
function notesOpen(){
    var m=document.getElementById('notesModal');
    if(m)m.style.display='flex';
    notesInit();
}
function notesClose(){
    var m=document.getElementById('notesModal');
    if(m)m.style.display='none';
}

function noteLoadAll(kw,tag){
    var url='/api/notes?sort=updated_desc';
    if(kw)url+='&q='+encodeURIComponent(kw);
    if(tag)url+='&tag='+encodeURIComponent(tag);
    fetch(url).then(function(r){return r.json()}).then(function(d){
        if(!d.ok)return;
        noteList=d.items||[];
        noteTags=d.tags||[];
        noteRenderList();
        noteRenderTags(tag||'');
    }).catch(function(e){console.error(e)});
}

function noteRenderList(){
    var el=document.getElementById('noteList');
    var empty=document.getElementById('noteListEmpty');
    if(!noteList||noteList.length===0){el.innerHTML='';empty.style.display='flex';return;}
    empty.style.display='none';
    el.innerHTML=noteList.map(function(n){
        var preview=(n.content||'').replace(/[#*>\`_\[\]()!~-]/g,'').substring(0,60);
        var tagStr=n.tag?'<span class="ni-tag">'+escHtml(n.tag)+'</span>':'';
        var pinStr=n.pinned?'<span class="pin">&#128204;</span>':'';
        var colorAccent=n.color||'var(--cyan)';
        return '<div class="note-item'+(noteCurId==n.id?' active':'')+'" style="border-left-color:'+colorAccent+'" onclick="noteOpen('+n.id+')">'+
            '<div class="ni-title">'+pinStr+escHtml(n.title)+'</div>'+
            '<div class="ni-preview">'+escHtml(preview)+'</div>'+
            '<div class="ni-meta">'+escHtml((n.updated_at||'').substring(0,10))+tagStr+'</div>'+
            '<div class="ni-del" onclick="event.stopPropagation();noteConfirmDelete('+n.id+')">&#10005;</div>'+
        '</div>';
    }).join('');
}

function noteRenderTags(activeTag){
    var el=document.getElementById('noteTagBar');
    if(!noteTags||noteTags.length===0){el.innerHTML='';return;}
    var html='<span class="notes-tag'+(activeTag===''?' active':'')+'" onclick="noteFilterTag(\'\')">全部</span>';
    html+=noteTags.map(function(t){return '<span class="notes-tag'+(activeTag===t?' active':'')+'" onclick="noteFilterTag(\''+escAttr(t)+'\')">'+escHtml(t)+'</span>'}).join('');
    el.innerHTML=html;
}

function noteFilterTag(tag){
    noteLoadAll(document.getElementById('noteSearch').value,tag);
}

function noteSearch(){
    var kw=document.getElementById('noteSearch').value;
    noteLoadAll(kw,'');
}

function noteDebounceSearch(){
    clearTimeout(noteDebounceTimer);
    noteDebounceTimer=setTimeout(noteSearch,350);
}

function noteOpen(id){
    noteCurId=id;
    noteRenderList();
    fetch('/api/notes/'+id).then(function(r){return r.json()}).then(function(d){
        if(!d.ok||!d.note)return;
        var n=d.note;
        document.getElementById('noteEditorEmpty').style.display='none';
        var ed=document.getElementById('noteEditor');
        ed.style.display='flex';
        document.getElementById('noteTitle').value=n.title||'';
        document.getElementById('noteContent').value=n.content||'';
        document.getElementById('noteTag').value=n.tag||'';
        document.getElementById('noteCharCount').textContent=(n.content||'').length+' 字';
        document.getElementById('noteStatus').textContent='已加载';
        document.querySelectorAll('.notes-color').forEach(function(el){el.classList.toggle('active',el.dataset.c===n.color)});
        document.getElementById('notePinBtn').style.color=n.pinned?'var(--amber)':'var(--text3)';
        if(notePreviewMode)notePreviewUpdate();
    }).catch(function(e){console.error(e)});
}

function noteNew(){
    fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'未命名笔记',content:'',color:'#4a6fa5',tag:''})}).then(function(r){return r.json()}).then(function(d){
        if(d.ok){
            noteCurId=d.id;
            noteLoadAll();
            noteOpen(d.id);
        }
    }).catch(function(e){console.error(e)});
}

function noteSave(){
    if(!noteCurId)return;
    var title=document.getElementById('noteTitle').value.trim()||'未命名笔记';
    var content=document.getElementById('noteContent').value||'';
    var tag=document.getElementById('noteTag').value.trim()||'';
    var color='#4a6fa5';
    var active=document.querySelector('.notes-color.active');
    if(active)color=active.dataset.c;
    document.getElementById('noteStatus').textContent='保存中...';
    fetch('/api/notes/'+noteCurId,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title,content:content,tag:tag,color:color})}).then(function(r){return r.json()}).then(function(d){
        if(d.ok){
            document.getElementById('noteStatus').textContent='已保存 '+new Date().toLocaleTimeString();
            var actTag=document.querySelector('.notes-tag.active');
            noteLoadAll(document.getElementById('noteSearch').value,actTag&&actTag.textContent!=='全部'?actTag.textContent:'');
        }
    }).catch(function(e){console.error(e);document.getElementById('noteStatus').textContent='保存失败';});
}

function noteDelete(){
    if(!noteCurId)return;
    confirmDialog('确定删除这篇笔记？',{danger:true}).then(function(ok){
        if(!ok)return;
        fetch('/api/notes/'+noteCurId,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
            if(d.ok){
                noteCurId=null;
                document.getElementById('noteEditor').style.display='none';
                document.getElementById('noteEditorEmpty').style.display='flex';
                noteLoadAll();
            }
        }).catch(function(e){console.error(e)});
    });
}

function noteConfirmDelete(id){
    confirmDialog('确定删除这篇笔记？',{danger:true}).then(function(ok){
        if(!ok)return;
        fetch('/api/notes/'+id,{method:'DELETE'}).then(function(r){return r.json()}).then(function(d){
            if(d.ok){
                if(noteCurId===id){noteCurId=null;document.getElementById('noteEditor').style.display='none';document.getElementById('noteEditorEmpty').style.display='flex';}
                noteLoadAll();
            }
        }).catch(function(e){console.error(e)});
    });
}

function noteTogglePin(){
    if(!noteCurId)return;
    fetch('/api/notes/'+noteCurId+'/pin',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
        if(d.ok){
            var actTag=document.querySelector('.notes-tag.active');
            noteLoadAll(document.getElementById('noteSearch').value,actTag&&actTag.textContent!=='全部'?actTag.textContent:'');
        }
    }).catch(function(e){console.error(e)});
}

function noteInsert(before,after,lineStart){
    var ta=document.getElementById('noteContent');
    if(!ta)return;
    var s=ta.selectionStart;
    var e=ta.selectionEnd;
    var v=ta.value;
    if(lineStart){
        var ls=v.lastIndexOf('\n',s-1)+1;
        var selected=v.substring(ls,e);
        ta.value=v.substring(0,ls)+before+selected+after+v.substring(e);
        ta.selectionStart=ta.selectionEnd=ls+before.length+selected.length+after.length;
    } else {
        ta.value=v.substring(0,s)+before+v.substring(s,e)+after+v.substring(e);
        ta.focus();
        ta.selectionStart=s+before.length;
        ta.selectionEnd=s+before.length+(e-s);
    }
    noteCount();
}

function noteCount(){
    var ta=document.getElementById('noteContent');
    if(!ta)return;
    var n=ta.value.length;
    var cc=document.getElementById('noteCharCount');
    if(cc)cc.textContent=n+' 字';
}

document.addEventListener('click',function(e){
    if(e.target.classList&&e.target.classList.contains('notes-color')){
        document.querySelectorAll('.notes-color').forEach(function(el){el.classList.remove('active')});
        e.target.classList.add('active');
    }
});

document.addEventListener('keydown',function(e){
    // Ctrl/Cmd+S 保存：弹窗没开或编辑器没显示时不该拦按键
    if((e.ctrlKey||e.metaKey)&&e.key==='s'){
        var m=document.getElementById('notesModal');
        var ed=document.getElementById('noteEditor');
        if(m&&m.style.display!=='none'&&ed&&ed.style.display!=='none'){e.preventDefault();noteSave();}
    }
    // Esc 关闭笔记本弹窗；确认弹窗（.cdlg-mask）开着时 Esc 归它，避免两个弹窗一起收
    if(e.key==='Escape'){
        var mm=document.getElementById('notesModal');
        if(mm&&mm.style.display!=='none'&&!document.querySelector('.cdlg-mask')){notesClose();}
    }
});

function notePreviewToggle(){
    notePreviewMode=!notePreviewMode;
    var ta=document.getElementById('noteContent');
    var pv=document.getElementById('notePreview');
    var tb=document.getElementById('noteToolbar');
    var btn=document.getElementById('notePreviewBtn');
    if(!ta||!pv)return;
    if(notePreviewMode){
        notePreviewUpdate();
        ta.style.display='none';
        pv.style.display='block';
        if(tb)tb.style.display='none';
        if(btn)btn.style.color='var(--cyan)';
    } else {
        ta.style.display='block';
        pv.style.display='none';
        if(tb)tb.style.display='flex';
        if(btn)btn.style.color='var(--text3)';
    }
}

function notePreviewUpdate(){
    var md=document.getElementById('noteContent').value||'';
    var html=md
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/^### (.+)$/gm,'<h3>$1</h3>')
        .replace(/^## (.+)$/gm,'<h2>$1</h2>')
        .replace(/^# (.+)$/gm,'<h1>$1</h1>')
        .replace(/```([\s\S]*?)```/g,'<pre><code>$1</code></pre>')
        .replace(/`([^`]+)`/g,'<code>$1</code>')
        .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
        .replace(/\*(.+?)\*/g,'<em>$1</em>')
        .replace(/~~(.+?)~~/g,'<del>$1</del>')
        .replace(/^> (.+)$/gm,'<blockquote>$1</blockquote>')
        .replace(/^- (.+)$/gm,'<li>$1</li>')
        .replace(/^(\d+)\. (.+)$/gm,'<li>$2</li>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>')
        .replace(/!\[([^\]]*)\]\(([^)]+)\)/g,'<img src="$2" alt="$1" style="max-width:100%;border-radius:8px">')
        .replace(/^---$/gm,'<hr style="border:none;border-top:1px solid var(--border);margin:14px 0">')
        .replace(/\n/g,'<br>');
    var pv=document.getElementById('notePreview');
    if(pv)pv.innerHTML=html;
}

function escHtml(s){
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s){
    return String(s||'').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}



function fmtSize(n){
  if(n===null||n===undefined)return '?';
  n=Number(n);
  if(n<1024)return n+'B';
  if(n<1048576)return (n/1024).toFixed(1)+'KB';
  if(n<1073741824)return (n/1048576).toFixed(1)+'MB';
  return (n/1073741824).toFixed(2)+'GB';
}
function fmtTime(t){
  if(!t)return '';
  var d=new Date(t*1000);
  var p=function(x){return x<10?'0'+x:x};
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
