
// ============================================================
// 通用
// ============================================================
function toggleSidebar(){document.getElementById('sidebar').classList.toggle('collapsed')}
function switchPage(e,p){
  document.querySelectorAll('.page').forEach(function(x){x.classList.remove('active')});
  document.querySelectorAll('.mi').forEach(function(x){x.classList.remove('active')});
  var t=document.getElementById('page-'+p);if(t)t.classList.add('active');
  if(e&&e.currentTarget)e.currentTarget.classList.add('active');
  else{var mi=document.querySelector('.mi[data-page="'+p+'"]');if(mi)mi.classList.add('active')}
  sessionStorage.setItem('lastPage',p);
  if(p==='launcher'){cuiCheckStatus()}
  if(p==='models'){checkA2();loadModels();restoreDlState();mlOnEnter()}
  if(p==='plugins'){checkGit();loadPlugins();restoreCloneState();plLoadMirror();plOnEnter()}
  if(p==='input'){inInitView();browseInput('')}
  if(p==='output')browseOutput('')
  if(p==='settings')loadSF()
  if(p==='workflows'){wfInit()}
  if(p==='repair'){repairLoadPytorch();repairLoadComfyUIBranches();repairCheckStatus();repairLoadMirrorInfo()}
  else{rlogClose()}
  if(p!=='plugins')plLogClose();
}
