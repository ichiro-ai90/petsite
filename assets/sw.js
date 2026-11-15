// 値下がり通知の受け口（本文なしの通知を受けて、文面をサイトから取りに行く）
async function hid(e){const b=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(e));return[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,"0")).join("").slice(0,32)}
self.addEventListener("push",ev=>{ev.waitUntil((async()=>{let m={title:"ペット用品 最安ナビ",body:"見た商品の価格が変わりました",url:"/favorites.html"};
 try{const s=await self.registration.pushManager.getSubscription();if(s){const r=await fetch("/api/notice?h="+await hid(s.endpoint),{cache:"no-store"});if(r.ok)m=await r.json()}}catch(e){}
 await self.registration.showNotification(m.title,{body:m.body,icon:"/favicon.svg",data:{url:m.url}})})())});
self.addEventListener("notificationclick",ev=>{ev.notification.close();ev.waitUntil(clients.openWindow((ev.notification.data&&ev.notification.data.url)||"/"))});
