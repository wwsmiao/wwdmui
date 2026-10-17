
// ============================================================
// 横幅
// ============================================================
function updateBanner(){
  Promise.all([
    fetch('/api/models?page_size=1').then(function(r){return r.json()}),
    fetch('/api/plugins?page_size=1').then(function(r){return r.json()})
  ]).then(function(arr){
    var mc=arr[0].total||0, pc=arr[1].total||0;
    document.querySelectorAll('#bCntM,#bCntM2').forEach(function(e){e.textContent=mc});
    document.querySelectorAll('#bCntP,#bCntP2').forEach(function(e){e.textContent=pc});
  }).catch(function(){});
}
updateBanner();setInterval(function(){if(!document.hidden)updateBanner()},30000);
