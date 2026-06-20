const C='sp-v5';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(ks=>
  Promise.all(ks.filter(k=>k!==C).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  const url=new URL(e.request.url);
  // network-first للصفحة الرئيسية والـ JS/manifest حتى تظهر التحديثات فوراً
  const netFirst=url.pathname.endsWith('/')||url.pathname.endsWith('.html')||
    url.pathname.endsWith('sw.js')||url.pathname.endsWith('manifest.json');
  if(netFirst){
    e.respondWith(fetch(e.request).then(x=>{caches.open(C).then(c=>c.put(e.request,x.clone()));return x;})
      .catch(()=>caches.match(e.request)));
    return;
  }
  // cache-first للأصول الثابتة (أيقونة)
  e.respondWith(caches.open(C).then(c=>c.match(e.request).then(r=>r||fetch(e.request)
    .then(x=>{c.put(e.request,x.clone());return x}).catch(()=>r)))));
});