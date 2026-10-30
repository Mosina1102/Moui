/* ===== 墨忆MOUI 面板 × 启动器 全量真实桥接（launcher 注入，勿手改面板文件） =====
   架构：pywebview.api（LauncherApi + KBridge 透传）
   实化范围：状态/大脑/日志/更新/向导 + 总览/聊天/关系/记忆6tab/心流/打分流/日常/人设/配置中心
   铁律：全部 fail-open —— 她没上线就显示占位，绝不炸面板 */
(function(){
 if (window.pywebview && window.pywebview.api) { boot(); }
 else { window.addEventListener('pywebviewready', boot); setTimeout(function(){ if(window.pywebview&&window.pywebview.api)boot(); }, 1200); }

 var booted=false;
 function boot(){
  if(booted)return; booted=true;
  var api=window.pywebview.api;
  // 0) 工具
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
  function ago(t){if(!t)return '—';var d=Date.now()/1000-t;if(d<60)return Math.max(1,d|0)+' 秒前';if(d<3600)return (d/60|0)+' 分钟前';if(d<86400)return (d/3600|0)+' 小时前';return (d/86400|0)+' 天前'}
  function fmtT(t){if(!t)return '—';var d=new Date(t*1000);return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
  function fmtD(t){if(!t)return '—';var d=new Date(t*1000);return (d.getMonth()+1)+'-'+('0'+d.getDate()).slice(-2)+' '+fmtT(t)}
  function off(r){return !r||r.ok===false}
  window.__esc=esc;
  // 1) 干掉所有演示定时器（原型的假数据流从此停播）
  for(var i=1;i<99999;i++){clearInterval(i);clearTimeout(i);}
  // 2) 版本戳 + QQ
  api.status().then(function(s){
    if(!s||!s.version)return;
    var h=document.querySelector('#pg-upd h2');
    if(h)h.innerHTML='墨忆MOUI <span class="sub" style="font-weight:400">v'+esc(s.version)+'</span>';
    window.__ver=s.version;
  });
  var topVer=document.getElementById('top-ver');
  if(topVer)topVer.textContent='墨忆MOUI v'+(window.__ver||'');
  // 3) 首启向导：config 未就绪才显示
  function paintWizard(s){var w=document.getElementById('wizard');if(w)w.style.display=(s&&s.wizard)?'flex':'none';}
  // 4) 四进程状态：真实灯
  var dotMap={running:'g',starting:'y',stopped:'r',error:'r'};
  function paintStatus(s){
    if(!s)return;
    paintWizard(s);
    var rows=document.querySelectorAll('.procs .proc');
    var order=['mongod','adapter','napcat','bot'];
    rows.forEach(function(r,i){
      var st=s[order[i]]||'stopped';
      var dot=r.querySelector('.dot'); if(dot){dot.className='dot '+(dotMap[st]||'r');dot.style.cssText='';}   // cssText 清空：防任何内联样式残留压过状态色
      var sub=r.querySelector('.sub');
      if(sub){ if(order[i]==='napcat')sub.textContent='进程+连接';
               else if(order[i]==='mongod')sub.textContent='27017';
               else if(order[i]==='adapter')sub.textContent='8095';
               else sub.textContent='8000'; }
    });
    var guard=document.querySelector('.procs');
    if(guard&&guard.parentElement){
      var p=guard.parentElement.querySelector('p.sub');
      if(p)p.textContent= (s.updating?'正在更新，别慌…':'守护已开启 · bot/适配器崩溃将自动拉起（60s 退避）');
    }
    paintPower(s);
    // 她刚上线的瞬间：全页真数据拉一遍（离线期接口失败留的占位全部刷新掉）
    if(window.__lastBotSt && window.__lastBotSt!=='running' && s.bot==='running'){
      try{pollHome()}catch(e){}
      try{loadChats()}catch(e){}
    }
    window.__lastBotSt=s.bot;
  }
  function pollStatus(){ api.status().then(paintStatus); }
  setInterval(pollStatus,1200);pollStatus();
  // hero 区「上线/下线」按钮（改造原型"戳她一下"演示按钮）；config 就绪自动上线保留，按钮随时手动控制
  function paintPower(s){
    var heroBtn=document.getElementById('btn-power');
    if(!heroBtn){
      heroBtn=document.querySelector('#pg-home .hero button');
      if(!heroBtn)return;
      heroBtn.id='btn-power';
    }
    var running=(s.bot==='running'),starting=(s.bot==='starting');
    heroBtn.disabled=!!starting||!!s.updating;
    heroBtn.className='btn '+(running?'danger':'primary');
    heroBtn.textContent=starting?'启动中…':(running?'让她下线':'让她上线');
    heroBtn.onclick=function(){
      if(heroBtn.disabled)return;
      heroBtn.disabled=true;
      heroBtn.textContent=running?'下线中…':'启动中…';
      toast(running?'下线序列启动：先停适配器再停主程序，她的记忆都落库了':'上线序列启动：MongoDB → 主程序 → 适配器 → NapCat');
      (running?api.stop_all():api.start_all()).then(function(){setTimeout(pollStatus,1200);});
    };
  }
  // 4.5) QQ 扫码弹窗：NapCat 等登录时面板中央弹二维码（读 NapCat 落的 qrcode.png），扫上自动关
  var qrOverlay=null,qrWasNeed=false;
  function hideQR(){
    if(qrOverlay){qrOverlay.style.display='none';}
  }
  function showQR(r){
    if(!qrOverlay){
      qrOverlay=document.createElement('div');
      qrOverlay.id='qr-login';
      qrOverlay.style.cssText='position:fixed;inset:0;background:rgba(8,10,16,.78);z-index:200;display:none;align-items:center;justify-content:center';
      qrOverlay.innerHTML='<div class="card" style="width:340px;max-width:90vw;text-align:center;padding:26px">'+
        '<h3 style="margin-bottom:4px">她的 QQ 需要扫码登录</h3>'+
        '<p class="sub" style="margin-bottom:14px">用手机 QQ 扫这个码，在手机上点授权</p>'+
        '<div id="qr-imgbox" style="background:#fff;border-radius:12px;padding:10px;display:inline-block"><img id="qr-img" style="width:240px;height:240px;display:block" alt="二维码"></div>'+
        '<p class="sub" id="qr-note" style="margin-top:12px"></p>'+
        '<div style="display:flex;gap:8px;justify-content:center;margin-top:10px">'+
        '<a id="qr-webui" class="btn" style="text-decoration:none;font-size:12px" target="_blank">打开 NapCat 管理页</a>'+
        '<button class="btn" id="qr-later">稍后再说</button></div></div>';
      document.body.appendChild(qrOverlay);
      document.getElementById('qr-later').onclick=function(){
        hideQR();window.__qrDismissed=true;
        toast('好，需要扫码时在「总览」页点她的状态灯，或等下次提醒');
      };
    }
    var img=document.getElementById('qr-img'),note=document.getElementById('qr-note'),wu=document.getElementById('qr-webui');
    if(r.qr){img.src=r.qr;img.style.display='block';}
    else{img.style.display='none';}
    if(note)note.textContent=r.note||(r.url?('二维码加载失败？复制到手机浏览器打开：'+r.url):'');
    if(wu&&r.webui)wu.href=r.webui;
    qrOverlay.style.display='flex';
  }
  function pollQR(){
    var wiz=document.getElementById('wizard');
    if(wiz&&wiz.style.display!=='none'){return}   // 向导期间不弹扫码
    api.qr_status().then(function(r){
      var need=!!(r&&r.ok!==false&&r.need);
      if(need){
        if(!window.__qrDismissed)showQR(r||{});
        qrWasNeed=true;
      }else{
        hideQR();
        window.__qrDismissed=false;
        if(qrWasNeed){qrWasNeed=false;if(r&&r.logged_in)toast('她登上 QQ 了');}
      }
    });
  }
  setInterval(pollQR,3000);pollQR();
  // 4.7) 品牌 logo 走 API 桥直供（真机实锤：<base> file:// 与 data URI 内联都被 WebView2 拦，logo 裂图）
  // 全量替换：原型里 logo img 不止侧栏一处（更新页还有一张），querySelectorAll 一网打尽
  api.logo_b64 && api.logo_b64().then(function(b){
    if(!b)return;
    var imgs=document.querySelectorAll('img');
    imgs.forEach(function(im){
      if((im.getAttribute('src')||'').indexOf('Moui_logo.png')>=0)im.src='data:image/png;base64,'+b;
    });
  });
  // 4.7.5) 私聊（PFC）开关显性化（老大拍板 2026-10-30）：实验性组改名+补搜索关键词——
  // 出厂 enable_friend_chat/pfc_chatting 全关，私聊她不回的头号原因，必须一眼能找到
  (function(){
    var secs=document.querySelectorAll('#pg-cfg details.cfgsec');
    secs.forEach(function(d){
      var kw=d.dataset.kw||'';
      if(kw.indexOf('实验')>=0&&kw.indexOf('pfc')<0){
        d.dataset.kw=kw+' pfc 私聊 好友 friend_chat enable_friend_chat';
        var sm=d.querySelector('summary');
        if(sm)sm.innerHTML='私聊（PFC）与实验性<span class="bdg y">自动重启</span><span class="chev">›</span>';
      }
    });
  })();
  // 4.8) 开发者 K 面板入口（老大拍板 2026-10-30：K面板作为指令打开给开发者观察，MOUI 面板加入口窗口）
  (function(){
    var side=document.getElementById('side');if(!side)return;
    var box=side.lastElementChild;if(!box)return;
    var a=document.createElement('a');
    a.href='javascript:void(0)';
    a.style.cssText='display:flex;gap:10px;padding:10px 12px;color:var(--sub);border-radius:10px;font-size:13px';
    a.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3h18v18H3z"/><path d="M3 9h18M9 21V9"/></svg>开发者 K 面板';
    a.onclick=function(){
      api.open_dashboard().then(function(r){
        if(r&&r.ok)toast(r.msg||'K 面板已打开');
        else toast((r&&r.error)||'打开失败',true);
      });
    };
    box.appendChild(a);
  })();
  // 5) 她的大脑：真事件流驱动（E3.5 events.jsonl）
  var nodeDot={message_in:'n0',willing:'n1',think_start:'n2',memory:'n3',tool_call:'n4',mood:'n5',review:'n6',send_out:'n7',think_end:'n2'};
  function ts(){var d=new Date();return d.toTimeString().slice(0,8)}
  function push(ev){
    var dot=document.getElementById(nodeDot[ev.node]);
    if(dot){dot.classList.add('lit');setTimeout(function(){dot.classList.remove('lit')},1200);}
    var det=ev.detail||{};
    var bits=Object.keys(det).map(function(k){return k+'='+String(det[k]).slice(0,40)});
    var txt=bits.join(' · ')||'…';
    var d=document.createElement('div');d.className='evtitem';
    d.innerHTML='<span class="ts">'+ts()+'</span><span class="n">'+esc(ev.node)+'</span><br>'+esc(txt);
    var list=$('#evtlist');if(list){list.prepend(d);if(list.children.length>30)list.lastChild.remove();}
    var now=$('#brain-now');if(now)now.textContent=ev.node+' · '+txt;
  }
  function pollEvents(){ api.poll_events().then(function(evs){ (evs||[]).forEach(push); }); }
  setInterval(pollEvents,700);pollEvents();
  // 6) 日志页：真实日志流
  var curStream='MaiCore';
  var sel=document.querySelector('#pg-log select');
  if(sel){sel.onchange=function(){curStream=sel.value;pullLogs();};}
  function pullLogs(){
    api.logs(curStream,120).then(function(text){
      var t=$('#term');if(!t||!text)return;
      t.innerHTML='';
      String(text).split('\n').forEach(function(ln){
        if(!ln.trim())return;
        var d=document.createElement('div');d.className='lg';
        var cls=/ERROR|CRITICAL|失败|Traceback/.test(ln)?'e':(/WARNING/.test(ln)?'w':'i');
        d.innerHTML='<span class="i">['+cls.toUpperCase()+(cls==='e'?'R!':'INFO')+']</span> <span class="m">'+ln.replace(/</g,'&lt;').slice(0,300)+'</span>';
        t.appendChild(d);
      });
      if($('#pg-log').classList.contains('act'))t.scrollTop=1e6;
    });
  }
  setInterval(pullLogs,2000);pullLogs();
  // 7) 更新页：真检查
  window.checkUpdate=function(){
    var b=$('#upd-btn');if(!b)return;b.textContent='检查中…';b.disabled=true;
    api.check_update().then(function(info){
      b.textContent='检查更新';b.disabled=false;
      var r=$('#upd-result');if(!r)return;
      if(!info){r.innerHTML='';return;}
      if(info.status==='available'){
        r.innerHTML='<div class="card" style="background:var(--card2);text-align:left;border-color:var(--purple)">'+
          '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px"><b>发现新版本 v'+esc(info.version)+'</b><span class="tag on">推荐更新</span></div>'+
          '<p class="sub">正在后台下载并静默安装，她的记忆一条不丢…</p><div class="bar"><i style="width:60%"></i></div></div>';
      }else if(info.status==='none'){
        r.innerHTML='<div class="card" style="background:var(--card2);text-align:left"><p class="sub">已是最新版本 v'+esc(window.__ver||'')+'。</p></div>';
      }else if(info.status==='blocked'){
        r.innerHTML='<div class="card" style="background:var(--card2);text-align:left;border-color:var(--pink)"><p class="sub">'+esc(info.notes||'这是个大版本更新，需要搬家。')+'</p></div>';
      }else{
        r.innerHTML='<div class="card" style="background:var(--card2);text-align:left"><p class="sub">检查失败：'+esc(info.notes||'网络不佳')+'</p></div>';
      }
    });
  };
  window.__updFail=function(msg){
    var r=$('#upd-result');if(r)r.innerHTML='<div class="card" style="background:var(--card2);text-align:left"><p class="sub">更新失败：'+esc(msg)+'（先用着现在的版本）</p></div>';
  };

  /* =============== 8) 总览页接真 =============== */
  function offLine(el,txt){if(el)el.innerHTML='<p class="sub">'+esc(txt||'她还没上线，启动后这里就有真数据了')+'</p>'}
  function paintHome(o,mo,ds){
    // hero
    var nameEl=document.querySelector('#pg-home .hero b');
    if(nameEl&&window.__cfg){nameEl.textContent=window.__cfg.nickname||'Moui';}
    var avaEl=document.querySelector('#pg-home .hero .ava');
    if(avaEl&&window.__cfg&&window.__cfg.nickname){avaEl.textContent=(window.__cfg.nickname||'M').charAt(0);}
    var st=window.__lastStatus||{};
    var tag=document.querySelector('#pg-home .hero .tag');
    if(tag){tag.textContent=st.bot==='running'?'在线 · 心流运转中':(st.bot==='starting'?'启动中…':'离线 · 点「让她上线」唤醒她');tag.className='tag'+(st.bot==='running'?' on':'');}
    var heroSub=document.querySelector('#pg-home .hero .sub');
    if(heroSub){
      var up=st.uptime||0;
      var uptxt=up?(up>=3600?('已连续运行 '+Math.floor(up/3600)+' 小时 '+Math.floor(up%3600/60)+' 分钟'):('已连续运行 '+Math.floor(up/60)+' 分钟')):'她现在离线';
      heroSub.textContent='QQ '+(window.__botQQ||'—')+' · '+uptxt;
    }
    // 四格统计
    var nums=document.querySelectorAll('#pg-home .stat4')[0];
    if(nums&&ds&&ds.ok!==false){
      var v=nums.querySelectorAll('.num');
      if(v[0])v[0].textContent=ds.recv||0;
      if(v[1])v[1].textContent=ds.reply||0;
      if(v[2])v[2].textContent=ds.rejected||0;
      if(v[3])v[3].textContent=ds.memory||0;
      // 原型演示副文案（群聊 1,102 · 私聊 182 等）接成真说明——真机实锤数字真了注释还是假的
      var sts=nums.querySelectorAll('.stat');
      var subTxt=['今日收到（群聊+私聊）','她主动发出的回复','审核器拦下的条数','今天新写入的记忆'];
      sts.forEach(function(stc,i){ var ss=stc.querySelectorAll('.sub'); if(subTxt[i]&&ss.length>1)ss[ss.length-1].textContent=subTxt[i]; });
    }
    // 情绪坐标
    if(mo&&mo.ok!==false){
      var dot=document.getElementById('mood-dot');
      if(dot){dot.style.left=((mo.valence+1)/2*100)+'%';dot.style.top=((1-(mo.arousal+1)/2)*100)+'%';}
      var p=dot&&dot.parentElement&&dot.parentElement.parentElement.querySelector('p.sub');
      if(p)p.innerHTML='愉悦 <b style="color:var(--pink)">'+(mo.valence>=0?'+':'')+mo.valence+'</b> · 唤醒 <b style="color:var(--purple)">'+(mo.arousal>=0?'+':'')+mo.arousal+'</b> —— '+esc(mo.reason||'她没说为什么');
    }
  }
  function paintTiles(o){
    var tiles=document.querySelectorAll('#pg-home .ktile');
    if(!tiles.length)return;
    if(!o||o.ok===false){ // 她没上线：原型写死的假数字全部换成占位
      tiles.forEach(function(t){
        var v=t.querySelector('.v');if(v)v.textContent='—';
        var s=t.querySelector('.s');if(s)s.textContent='她还没上线，启动后就有真数据';
      });
      return;
    }
    if(o.budget){
      var t=tiles[0].querySelector('.v');
      if(t)t.innerHTML='¥'+o.budget.used+'<span style="font-size:13px;color:var(--sub)"> / '+(o.budget.daily>0?'¥'+o.budget.daily:'不限')+'</span>';
      var s=tiles[0].querySelector('.s');
      if(s)s.innerHTML='<span class="bdg '+(o.budget.tier==='ok'?'g':(o.budget.tier==='unlimited'?'':'y'))+'">'+(o.budget.tier==='ok'?'余量充足':(o.budget.tier==='unlimited'?'未设预算':(o.budget.tier==='warning'?'已限流（≥80%）':'已打满熔断')))+'</span> · 点「她的日常」看账本';
    }
    if(o&&o.ok!==false&&o.purpose){
      var t1=tiles[1].querySelector('.v');
      if(t1)t1.innerHTML=(o.purpose.count_today||0)+'<span style="font-size:13px;color:var(--sub)"> / '+(o.purpose.daily_max||0)+'</span>';
      var s1=tiles[1].querySelector('.s');
      if(s1)s1.textContent='惦记中 '+(o.purpose.pending_n||0)+' 条 · 素材不足不出门';
    }
    if(o&&o.ok!==false&&o.scores_today){
      var t2=tiles[2].querySelector('.v');
      if(t2)t2.textContent=o.scores_today.total||0;
      var s2=tiles[2].querySelector('.s');
      if(s2)s2.innerHTML='通过 '+(o.scores_today.passed||0)+' · 均分 '+(o.scores_today.avg||0)+' · <a href="javascript:go(\'scores\')" style="color:var(--purple)">看明细</a>';
    }
    if(o&&o.ok!==false&&o.backups&&o.backups.count){
      var t3=tiles[3].querySelector('.v');
      if(t3)t3.textContent=o.backups.count+' 份';
      var s3=tiles[3].querySelector('.s');
      if(s3)s3.textContent='最近 '+(o.backups.latest_name||'').replace('.zip','')+' · '+ago(o.backups.latest_time);
    }
  }
  function pollHome(){
    api.status().then(function(s){window.__lastStatus=s;paintHome(null,null,null);});
    api.kget('/api/k/overview').then(function(o){
      if(off(o)){paintTiles(null);return}
      paintTiles(o);
      if(o.mood)paintHome(null,o.mood,null);
    });
    api.kget('/api/k/mood').then(function(mo){ if(!off(mo)){paintHome(null,mo,null);window.__moodReason=mo.reason||'';} });
    api.day_stats().then(function(ds){ if(ds&&ds.ok!==false)paintHome(null,null,ds); });
  }
  setInterval(pollHome,8000);pollHome();

  /* =============== 9) 聊天记录接真（MongoDB） =============== */
  var CHATS=[],curChat='',chatCache={};
  function chatName(cid){var c=CHATS.find(function(x){return x.chat_id===cid});return c?c.name:('会话 '+cid.slice(-8))}
  function renderConvsReal(){
    var list=$('#convlist');if(!list)return;
    if(!CHATS.length){
      list.innerHTML='<p class="sub" style="padding:14px">'+(window.__lastBotSt==='running'?'还没有会话记录——她聊起来后这里就有':'她还没上线，聊天记录在她学习后出现')+'</p>';
      var ms=$('#msgs');if(ms&&!chatCache[curChat])ms.innerHTML='<div class="sysline">'+(window.__lastBotSt==='running'?'选一个会话，或等第一条消息到来':'她还没上线')+'</div>';
      return;
    }
    list.innerHTML=CHATS.map(function(c,i){
      var ch=(c.name||'?').charAt(0);
      var cls=['a-purple','a-cyan','a-yellow','a-pink','a-gray'][i%5];
      return '<div class="conv '+(c.chat_id===curChat?'sel':'')+'" onclick="openConv(\''+esc(c.chat_id)+'\')">'+
        '<div class="ava '+cls+'" style="width:38px;height:38px;font-size:15px">'+esc(ch)+'</div>'+
        '<div class="t"><b>'+esc(c.name)+'</b><span class="sub">'+(c.count||0)+' 条 · '+ago(c.last)+'</span></div></div>';
    }).join('')||'<p class="sub" style="padding:14px">还没有会话记录</p>';
  }
  function renderMsgs(kw){
    var box=$('#msgs');if(!box)return;
    var items=chatCache[curChat]||[];
    if(kw)items=items.filter(function(m){return (m.text||'').indexOf(kw)>=0||(m.who||'').indexOf(kw)>=0});
    var html='',lastT=0;
    var myQQ=String(window.__botQQ||'');
    items.slice().reverse().forEach(function(m){
      if(lastT&&m.time-lastT>1800)html+='<div class="daysep">—— '+fmtD(m.time)+' ——</div>';
      lastT=m.time;
      var me=myQQ&&m.uid===myQQ;
      var ch=(m.who||'?').charAt(0);
      if(me){
        html+='<div class="msg me"><div class="ava a-pink" style="width:30px;height:30px;font-size:12px">'+esc(ch)+'</div><div><div class="who" style="text-align:right">'+esc(m.who)+'</div><div class="bub">'+esc(m.text)+'</div></div></div>';
      }else{
        html+='<div class="msg"><div class="ava a-gray" style="width:30px;height:30px;font-size:12px">'+esc(ch)+'</div><div><div class="who">'+esc(m.who)+' · '+fmtT(m.time)+'</div><div class="bub">'+esc(m.text)+'</div></div></div>';
      }
    });
    box.innerHTML=html||'<div class="sysline">'+(kw?'这条流里没搜到「'+esc(kw)+'」':'暂无消息')+'</div>';
    box.scrollTop=1e6;
  }
  window.openConv=function(cid,kw){
    curChat=cid;renderConvsReal();
    var t=document.getElementById('chat-title');if(t)t.textContent=chatName(cid);
    if(kw!==undefined&&kw!==''){renderMsgs(kw);return}
    api.kget('/api/k/db/messages?chat_id='+encodeURIComponent(cid)+'&size=60').then(function(r){
      if(off(r)){ $('#msgs').innerHTML='<div class="sysline">'+esc(r.error||'读取失败')+'</div>';return}
      chatCache[cid]=r.items||[];
      renderMsgs('');
    });
  };
  function loadChats(){
    var list=$('#convlist');if(list)list.innerHTML='<p class="sub" style="padding:14px">读取中…</p>';
    api.kget('/api/k/db/chats?limit=30').then(function(r){
      if(off(r)){renderConvsReal();return}   // 离线也把原型假会话列表清成占位
      CHATS=r.items||[];
      if(!curChat&&CHATS.length)curChat=CHATS[0].chat_id;
      // 真机实锤：没有会话时右侧标题还挂着原型的「麦麦开发交流群」——同步清掉
      var ct=document.getElementById('chat-title');
      if(ct)ct.textContent=CHATS.length?chatName(curChat):'暂无会话';
      renderConvsReal();
      if(curChat)openConv(curChat);
    });
  }
  var chatSearch=document.querySelector('#pg-chat .chathead input');
  if(chatSearch)chatSearch.oninput=function(){renderMsgs(chatSearch.value.trim())};

  /* =============== 10) 关系页接真（person_info） =============== */
  function lvl(a){a=+a||0;return a>=900?'挚爱':a>=750?'亲密':a>=300?'熟悉':a>=100?'认识':(a<0?'冷淡':'陌生')}
  window.saveAff=function(pid,btn){
    var card=btn.closest('.rel');
    var inp=card.querySelector('input[type=number]');
    var v=parseFloat(inp.value);
    if(isNaN(v)||v<-1000||v>1000){toast('好感度要在 -1000 ~ 1000 之间喵');return}
    api.kpost('/api/k/db/person/update',{person_id:pid,relationship_value:v}).then(function(r){
      toast(r.msg||r.error||'已保存',r.ok===false);
      if(r.ok!==false){var num=card.querySelector('.aff-num');if(num)num.textContent=v;
        var bar=card.querySelector('.bar i');if(bar)bar.style.width=Math.max(0,Math.min(100,v/10))+'%';
        var tag=card.querySelector('.tag');if(tag)tag.textContent=lvl(v);}
    });
  };
  function loadRels(){
    var box=$('#rels');if(!box)return;
    box.innerHTML='<p class="sub" style="padding:14px">读取中…</p>';
    api.kget('/api/k/person_info?size=20').then(function(r){
      if(off(r)){box.innerHTML='<p class="sub">她还没上线，认识的人会在聊天后出现</p>';return}
      var items=r.items||[];
      if(!items.length){box.innerHTML='<p class="sub">还没有认识的人——等她在群里聊起来就有啦</p>';return}
      box.innerHTML=items.map(function(p){
        var aff=p.relationship_value||0;
        var ch=(p.name||'?').charAt(0);
        var cls=['a-pink','a-purple','a-cyan','a-yellow','a-gray'][Math.abs((p.person_id||'').length)%5];
        return '<div class="card rel"><div class="ava '+cls+'" style="width:52px;height:52px;font-size:19px">'+esc(ch)+'</div>'+
          '<div style="flex:1"><div style="display:flex;align-items:center;gap:8px"><b>'+esc(p.name)+'</b><span class="tag">'+lvl(aff)+'</span></div>'+
          '<span class="sub">'+esc(p.person_id)+' · 认识 '+ago(p.konw_time)+' · 最近消息间隔 '+(p.msg_interval||0)+' 条</span>'+
          '<div class="bar"><i style="width:'+Math.max(0,Math.min(100,aff/10))+'%"></i></div>'+
          '<div style="display:flex;align-items:baseline;gap:8px"><span class="aff-num">'+aff+'</span><span class="sub">/ 1000</span></div>'+
          '<div style="display:flex;gap:8px;align-items:center;margin:6px 0">'+
          '<input type="number" value="'+aff+'" min="-1000" max="1000" style="width:90px;padding:5px 10px">'+
          '<button class="btn" onclick="saveAff(\''+esc(p.person_id)+'\',this)">改好感（即时生效）</button>'+
          '<span class="sub" style="font-size:11px">负=冷淡 正=亲密 · 她的热情跟着这个走</span></div></div></div>';
      }).join('');
    });
  }

  /* =============== 11) 记忆页 6 tab 接真 =============== */
  window.memTab=function(t,el){
    document.querySelectorAll('#pg-mem .tag').forEach(function(x){x.classList.remove('on')});
    if(el)el.classList.add('on');
    var b=$('#mem-body');
    if(!b)return;
    b.innerHTML='<p class="sub" style="padding:10px">读取中…</p>';
    if(t==='self'){
      api.kget('/api/k/entries').then(function(r){
        if(off(r)){b.innerHTML='<p class="sub">她还没上线</p>';return}
        var E=r.entries||[];
        b.innerHTML='<div class="card"><h3>活跃条目（归档区另有 '+(r.archive_count||0)+' 条）</h3>'+
          (E.length?E.map(function(m,i){
            var cat=m.category==='experience'?'经验':(m.category==='event'?'事件':'认知');
            return '<div class="entry" style="display:flex;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)">'+
              '<span class="tag '+(m.category==='experience'?'on':'')+'" style="font-size:11px;height:fit-content">'+cat+'</span>'+
              '<div style="flex:1"><div style="font-size:13px">'+esc(m.content||m.text||'')+'</div>'+
              '<div class="sub" style="font-size:11.5px">强度 '+Math.round((m.strength||0.5)*100)+'% · '+ago(m.created_at||m.updated_at)+'</div></div>'+
              '<button class="btn danger" style="padding:3px 10px;font-size:12px" onclick="delMemEntry(this)">删</button></div>';
          }).join(''):'<p class="sub">还没有自我认知条目——她聊几天就会开始总结自己</p>')+
          '<p class="sub" style="margin-top:10px">删除进归档区可恢复 · 全部增改走 journal 留痕</p></div>';
      });
    }else if(t==='cards'){
      api.kget('/api/k/person_cards').then(function(r){
        if(off(r)){b.innerHTML='<p class="sub">她还没上线</p>';return}
        var C=r.cards||[];
        b.innerHTML=C.length?C.map(function(c){
          return '<div class="card" style="margin-bottom:12px" data-qq="'+esc(c.qq)+'">'+
            '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>'+esc(c.name)+'</b><span class="sub">QQ '+esc(c.qq)+'</span><span class="tag" style="margin-left:auto">'+(c.events_count||0)+' 事件 · '+ago(c.updated_at)+'</span></div>'+
            '<div class="cfgline"><span class="fk">印象</span><input value="'+esc(c.impression)+'"><button class="btn" onclick="saveCard(this,\'impression\')">存</button></div>'+
            '<div class="cfgline"><span class="fk">相处</span><input value="'+esc(c.interaction)+'"><button class="btn" onclick="saveCard(this,\'interaction\')">存</button></div>'+
            '<p class="sub" style="font-size:12px">雷点：'+esc((c.pet_peeves||[]).join('、')||'暂无')+'</p>'+
            (c.events||[]).slice(-3).map(function(e){return '<p class="sub" style="font-size:12px">· '+esc(typeof e==='string'?e:(e.summary||e.text||JSON.stringify(e).slice(0,80)))+'</p>'}).join('')+
            '</div>';
        }).join(''):'<div class="card"><p class="sub">还没有人物卡——她跟谁聊出印象了就会建档（方案L）</p></div>';
      });
    }else if(t==='epi'){
      api.kget('/api/k/episodic_cards?limit=50').then(function(r){
        if(off(r)){b.innerHTML='<p class="sub">她还没上线</p>';return}
        var C=r.cards||[];
        b.innerHTML='<div class="card"><h3>情节卡（共同回忆时间线 · 宁缺毋滥）</h3>'+
          (C.length?C.map(function(e){
            return '<div class="mem evt" style="margin-bottom:10px"><div class="meta"><span class="tag on" style="font-size:11px">重要度 '+esc(e.importance!=null?e.importance:'?')+'</span><span class="sub">'+fmtD(e.created_at)+'</span></div>'+
              '<div style="font-size:13.5px">'+esc(e.summary||e.title||e.content||'')+'</div>'+
              (e.evidence?'<div class="sub" style="font-size:12px;margin-top:4px">依据：'+esc(e.evidence)+'</div>':'')+'</div>';
          }).join(''):'<p class="sub">还没有情节卡——共同经历值得记住的时刻才会建档</p>')+'</div>';
      });
    }else if(t==='world'){
      api.kget('/api/k/world_cards').then(function(r){
        if(off(r)){b.innerHTML='<p class="sub">她还没上线</p>';return}
        var C=r.cards||[];
        b.innerHTML='<div class="card"><h3>世界卡（她知道的事 · 容量 '+(C.length||0)+'/'+(r.total||C.length||0)+'，闲逛时联想注入）</h3>'+
          (C.length?C.map(function(w){
            return '<div class="mem exp" style="margin-bottom:10px"><div style="font-size:13.5px">'+esc(w.content||w.fact||w.text||'')+'</div>'+
              '<div class="meta" style="margin-top:4px"><span class="sub" style="font-size:11.5px">学到于 '+ago(w.created_at)+(w.last_recalled?' · 上次想起 '+ago(w.last_recalled):'')+'</span></div></div>';
          }).join(''):'<p class="sub">她还没上网学到东西——开了闲逛之后这里会长出来</p>')+'</div>';
      });
    }else if(t==='imp'){
      api.kget('/api/k/impressions').then(function(r){
        if(off(r)){b.innerHTML='<p class="sub">她还没上线</p>';return}
        var D=r.data||{};
        var keys=Object.keys(D);
        b.innerHTML='<p class="sub" style="margin-bottom:10px">她对每个群的相处策略 · 每日 4 点档自动审查，也可手动触发</p>'+
          (keys.length?keys.map(function(sid){
            var x=D[sid]||{};
            var fields=Object.keys(x).filter(function(k){return typeof x[k]==='string'||typeof x[k]==='number'});
            return '<div class="card" style="margin-bottom:12px" data-sid="'+esc(sid)+'">'+
              '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>'+esc(x.group_name||chatName(sid))+'</b><span class="sub">'+esc(sid.slice(-10))+'</span>'+
              '<span class="sub" style="margin-left:auto">证据: '+esc(String(x.evidence||'-').slice(0,50))+' · '+ago(x.updated)+'</span></div>'+
              fields.filter(function(f){return f!=='group_name'&&f!=='evidence'&&f!=='updated'}).map(function(f){
                return '<div class="cfgline"><span class="fk">'+esc(f)+'</span><input value="'+esc(x[f])+'"></div>';
              }).join('')+
              '<div style="display:flex;gap:8px;margin-top:6px"><button class="btn" onclick="saveImp(this)">保存修改</button>'+
              '<button class="btn" onclick="reviewNow(\''+esc(sid)+'\')">立即审查</button>'+
              '<button class="btn" onclick="impHistory(\''+esc(sid)+'\',this)">演变</button></div></div>';
          }).join(''):'<div class="card"><p class="sub">还没有群像——每日审查跑起来后她会总结每个群怎么混（可在配置里开）</p></div>');
      });
    }else{ // graph
      b.innerHTML='<div class="card"><div style="display:flex;gap:10px;margin-bottom:10px"><input placeholder="搜索概念…" style="flex:1" id="mem-q"><button class="btn" onclick="searchGraph()">搜索</button></div><div id="graph-tbl"><p class="sub">读取中…</p></div></div>';
      api.kget('/api/k/db/memory?size=20').then(function(r){paintGraph(r,'')});
    }
  };
  window.searchGraph=function(){
    var q=(document.getElementById('mem-q')||{}).value||'';
    var tb=document.getElementById('graph-tbl');if(tb)tb.innerHTML='<p class="sub">搜索中…</p>';
    api.kget('/api/k/db/memory?q='+encodeURIComponent(q)+'&size=20').then(function(r){paintGraph(r,q)});
  };
  function paintGraph(r,q){
    var tb=document.getElementById('graph-tbl');if(!tb)return;
    if(off(r)){tb.innerHTML='<p class="sub">'+esc(r.error||'她还没上线')+'</p>';return}
    var items=r.items||[];
    tb.innerHTML=(items.length?'<table class="tbl"><tr><th>概念</th><th>记忆内容</th><th style="width:110px">操作</th></tr>'+
      items.map(function(g){
        return '<tr><td style="width:24%"><b style="font-size:13px">'+esc(g.concept)+'</b><div class="sub" style="font-size:11px">'+ago(g.last_modified)+'改过</div></td>'+
          '<td class="sub">'+(g.memory_items||[]).map(esc).join('<br>')+'</td>'+
          '<td><button class="btn" style="padding:3px 10px;font-size:12px" onclick="editMem(\''+esc(g.id)+'\',this)">编辑</button>'+
          '<button class="btn danger" style="padding:3px 10px;font-size:12px;margin-top:4px" onclick="delMem(\''+esc(g.id)+'\')">删</button></td></tr>';
      }).join('')+'</table>':'<p class="sub">'+(q?'没搜到「'+esc(q)+'」':'海马体还是空的——她聊天后自动建图谱')+'</p>')+
      '<p class="sub" style="margin-top:8px">共 '+r.total+' 个概念节点 · 第 '+r.page+' 页</p>';
  }
  window.editMem=function(id,btn){
    var tr=btn.closest('tr');var td=tr.children[1];
    if(td.querySelector('textarea'))return;
    var old=td.innerText;
    td.innerHTML='<textarea rows="4" style="font-size:12px">'+esc(old)+'</textarea>';
    btn.textContent='保存';btn.onclick=function(){
      var items=td.querySelector('textarea').value.split('\n').map(function(s){return s.trim()}).filter(Boolean);
      api.kpost('/api/k/db/memory/update',{id:id,memory_items:items}).then(function(r){
        toast(r.msg||r.error||'',r.ok===false);searchGraph();
      });
    };
  };
  window.delMem=function(id){
    api.kpost('/api/k/db/memory/delete',{id:id}).then(function(r){toast(r.msg||r.error||'',r.ok===false);searchGraph()});
  };
  window.delMemEntry=function(btn){
    var row=btn.closest('.entry');
    var content=row.querySelector('div:nth-child(2) div').textContent;
    api.kpost('/api/k/entries/delete',{content:content}).then(function(r){toast(r.msg||r.error||'',r.ok===false);if(r.ok!==false)row.style.opacity=.35});
  };
  window.saveCard=function(btn,field){
    var card=btn.closest('.card');var qq=card.dataset.qq;
    var body={qq:qq};body[field]=card.querySelector('.cfgline input').value;
    api.kpost('/api/k/person_cards/update',body).then(function(r){toast(r.ok!==false?'人物卡已更新':r.error,r.ok===false)});
  };
  window.saveImp=function(btn){
    var card=btn.closest('.card');var sid=card.dataset.sid;
    var inputs=card.querySelectorAll('.cfgline input');
    var first=inputs[0],fkey=first.closest('.cfgline').querySelector('.fk').textContent;
    api.kpost('/api/k/impressions/set',{stream_id:sid,field:fkey,value:first.value}).then(function(r){toast(r.msg||r.error||'',r.ok===false)});
  };
  window.reviewNow=function(sid){
    api.kpost('/api/k/action/review',{stream_id:sid}).then(function(r){toast(r.msg||r.error||'',r.ok===false)});
  };
  window.impHistory=function(sid,btn){
    api.kget('/api/k/imp_history?stream_id='+encodeURIComponent(sid)+'&limit=10').then(function(r){
      if(off(r)){toast(r.error||'读取失败',true);return}
      toast('认知演变 '+((r.items||[]).length)+' 条：'+(r.items||[]).slice(-3).map(function(x){return x.op}).join(' → ')||'暂无记录');
    });
  };

  /* =============== 12) 心流监控接真（事件流聚合 + 突发会话池） =============== */
  var selFlow='';
  function breathDur(k){return k>=8?1.6:k>=6?2.2:k>=4?3:k>=2?4.5:7}
  function renderFlowsReal(flows){
    var grid=$('#flowgrid');if(!grid)return;
    var gt=document.querySelector('#pg-flow .card .tag');
    if(gt){gt.textContent=flows.length?('全局状态 · '+flows.length+' 条流在运转'):'全局状态 · 待机中';gt.className='tag'+(flows.length?' on':'');}
    grid.innerHTML=flows.map(function(f){
      var nm=chatName(f.stream_id);
      var isSel=f.stream_id===selFlow;
      var pts=(f.hist||[]).slice(-24).map(function(v,i){var x=(i*120/23).toFixed(1);var y=(34-Math.max(0,Math.min(10,v))/10*30+1).toFixed(1);return x+','+y}).join(' ');
      var above=(f.k||0)>=10;
      return '<div class="flowcard '+((f.k||0)<3?'low ':'')+(isSel?'sel':'')+'" style="--bd:'+breathDur(f.k||0)+'s" onclick="selCard(\''+esc(f.stream_id)+'\')">'+
        '<div style="display:flex;align-items:center;gap:9px;margin-bottom:8px"><span class="fdot '+((f.k||0)<3?'low':'')+'"></span>'+
        '<div class="ava a-purple" style="width:30px;height:30px;font-size:12px">'+esc(nm.charAt(0))+'</div>'+
        '<div style="flex:1;min-width:0"><b style="font-size:13.5px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(nm)+'</b><span class="sub" style="font-size:11px">'+(f.msgs||0)+' 条消息 · '+ago(f.last)+'</span></div>'+
        '<div style="text-align:right"><div class="fk">'+(f.k||0).toFixed(1)+'</div><span class="sub" style="font-size:10.5px">激活度</span></div></div>'+
        '<svg viewBox="0 0 120 36" style="width:100%;height:36px;display:block"><polyline points="'+pts+'" fill="none" stroke="'+((f.k||0)>=3?'#f472b6':'#4b5563')+'" stroke-width="1.5"/></svg>'+
        '<div class="flowmind">'+esc(f.willing||'还没有意愿判定记录')+'</div>'+
        '<div style="margin-top:6px">'+(above?'<span class="tag on" style="font-size:11px">阈值以上 · 会抢话</span>':'<span class="tag" style="font-size:11px">围观中</span>')+'</div></div>';
    }).join('')||(window.__lastBotSt==='running'
      ? '<p class="sub">她在线，但各群都在沉睡——来点消息，这里会长出每条聊天流的呼吸</p>'
      : '<p class="sub">她还没上线——启动后这里会实时长出每条聊天流的呼吸</p>');
    var n=document.getElementById('subflow-n');if(n)n.textContent=flows.length;
    // 真机实锤：曲线详情卡只有在选中某条流时才该有内容——无选中（含首次进入、流全空）一律清成待机态，
    // 否则原型的假曲线/假想法/旧人名演示残留会一直挂着
    var selF=(flows||[]).find(function(f){return f.stream_id===selFlow});
    if(!selF){
      var ct=document.getElementById('chart-title');
      if(ct)ct.textContent=((window.__cfg&&window.__cfg.nickname)||'她')+' · 曲线详情';
      ['d-mind','d-state','d-above','d-last'].forEach(function(id){var e=document.getElementById(id);if(e)e.textContent='—';});
      var k0=document.getElementById('k-line');if(k0)k0.setAttribute('points','');
      var p0=document.getElementById('p-line');if(p0)p0.setAttribute('points','');
    }
  }
  window.selCard=function(sid){
    selFlow=sid;
    api.flow_status().then(function(r){
      if(off(r))return;
      renderFlowsReal(r.flows||[]);
      var f=(r.flows||[]).find(function(x){return x.stream_id===sid});
      if(!f)return;
      var nm=chatName(sid);
      $('#chart-title').textContent=nm+' · 曲线详情';
      $('#d-mind').textContent=f.willing||'—';
      $('#d-state').textContent=(f.k||0)>=10?'会抢话':(f.k||0)>=3?'围观':'潜水';
      $('#d-above').innerHTML=(f.k||0)>=10?'<b style="color:var(--green)">是</b>':'<b style="color:var(--sub)">否</b>';
      $('#d-last').textContent=ago(f.last);
      var pts=(f.hist||[]).map(function(v,i){var x=(i*Math.max(1,(f.hist.length-1))?(i*560/Math.max(1,f.hist.length-1)):0).toFixed(1);var y=(126-Math.max(0,Math.min(10,v))/10*118+2).toFixed(1);return x+','+y}).join(' ');
      var kl=document.getElementById('k-line');if(kl)kl.setAttribute('points',pts);
      var pl=document.getElementById('p-line');if(pl)pl.setAttribute('points','');
    });
  };
  function paintMindHist(flows,mainMind){
    var box=$('#mind-hist');if(!box)return;
    var main=document.getElementById('main-mind');
    if(main)main.textContent=mainMind||'跟着各群的节奏走';
    var rows=(flows||[]).filter(function(f){return f.willing}).slice(0,8);
    box.innerHTML=rows.length?rows.map(function(f,i){
      return '<div class="mem evt" style="margin-bottom:8px;'+(i?'opacity:.75':'')+'"><div class="meta"><span class="sub">'+esc(chatName(f.stream_id))+'</span>'+(i?'':'<span class="tag on" style="font-size:10.5px">最新</span>')+'<span class="sub" style="margin-left:auto">'+ago(f.last)+'</span></div><div style="font-size:13px">'+esc(f.willing)+'</div></div>';
    }).join(''):'<p class="sub">她还没开始判断要不要说话——启动并来点消息后这里就有戏了</p>';
  }
  function pollFlow(){
    if(!$('#pg-flow').classList.contains('act'))return;
    api.flow_status().then(function(r){
      if(off(r)){renderFlowsReal([]);paintMindHist([], '');return}   // 离线清掉原型假流卡
      if(!CHATS.length){api.kget('/api/k/db/chats?limit=30').then(function(c){if(!off(c)){CHATS=c.items||[];renderFlowsReal(r.flows||[]);paintMindHist(r.flows||[],r.main_mind)}});}
      else{renderFlowsReal(r.flows||[]);paintMindHist(r.flows||[],r.main_mind)}
    });
  }
  setInterval(pollFlow,3000);

  /* =============== 13) 打分流接真 =============== */
  var scoreDays=1;
  function mapPts(arr,y0,y1){return arr.map(function(v,i){return (30+i*(590/Math.max(1,arr.length-1))).toFixed(1)+','+(185-(v-y0)/(y1-y0)*160).toFixed(1)}).join(' ')}
  function loadScores(){
    if(!$('#pg-scores').classList.contains('act'))return;
    var d=scoreDays;
    api.kget('/api/k/scores?days='+d).then(function(r){
      var head=$('#pg-scores .card .sub');
      var rows=$('#score-rows');
      if(off(r)){
        if(head)head.textContent='她还没上线';
        if(rows)rows.innerHTML='<tr><td colspan="6" class="sub">她还没上线，启动后这里记录她每句话的审核结果</td></tr>';
        return;
      }
      if(head)head.innerHTML='共 <b style="color:var(--txt)">'+(r.total||0)+'</b> 条 · 通过率 <b style="color:var(--green)">'+(r.pass_rate||0)+'%</b> · 均分 <b style="color:var(--pink)">'+(r.avg||0)+'</b>';
      if(rows)rows.innerHTML=(r.events||[]).slice().reverse().map(function(e){
        return '<tr><td>'+esc(e.time||fmtD(e.ts))+'</td><td>'+esc(e.group_name||e.stream_id||'?')+'</td>'+
          '<td>'+(e.passed?'<span class="bdg g">通过</span>':'<span class="bdg r">拒发</span>')+'</td>'+
          '<td><b>'+esc(e.score)+'</b></td><td>'+esc(String(e.reply_head||e.reply||'').slice(0,40))+'</td><td class="sub">'+esc(e.reason||'')+'</td></tr>';
      }).join('')||'<tr><td colspan="6" class="sub">还没有打分记录</td></tr>';
      var gtbl=(r.groups||[]).map(function(g){return '<tr><td>'+esc(g.group)+'</td><td>'+g.total+'</td><td>'+g.passed+'</td><td>'+g.avg+'</td></tr>'}).join('');
      var gcard=document.querySelectorAll('#pg-scores .row2 .card')[2];
      if(gcard){var tbl=gcard.querySelector('table');
        if(tbl)tbl.innerHTML='<tr><th>群</th><th>条数</th><th>通过</th><th>均分</th></tr>'+(gtbl||'<tr><td colspan="4" class="sub">暂无</td></tr>');}
    });
    api.kget('/api/k/reason_dist?days='+d).then(function(r){
      var box=$('#rej-bars');if(!box)return;
      if(off(r)){box.innerHTML='<p class="sub">她还没上线</p>';return}
      var mx=Math.max(1,(r.dist||[]).reduce(function(m,x){return Math.max(m,x[1])},0));
      var head=box.closest('.card').querySelector('h3');
      if(head)head.innerHTML='拒发原因分布（共 '+(r.rejected||0)+' 条被拒）';
      box.innerHTML=(r.dist||[]).length?r.dist.map(function(x){
        return '<div class="hbar"><span class="lb">'+esc(x[0])+'</span><div class="tr"><div class="fi" style="width:'+(x[1]/mx*100)+'%;background:var(--red)">'+x[1]+'</div></div></div>';
      }).join(''):'<p class="sub">零拒发——她说的每句话都过审了</p>';
    });
    api.kget('/api/k/score_trend?days='+d).then(function(r){
      if(off(r)){
        var a0=document.getElementById('trend-avg'),t0=document.getElementById('trend-rate');
        if(a0)a0.setAttribute('points','');if(t0)t0.setAttribute('points','');
        return;
      }
      var T=r.trend||[];
      var h=$('#pg-scores h3');
      // 真机实锤：原型静态「近 14 天」与选择器（近1天）不同步——标题无条件跟选择器走
      if(h)h.textContent='质量趋势（近 '+d+' 天'+(T.length?' · '+T.length+' 个时段':' · 暂无数据')+'）';
      var avgs=T.map(function(x){return x.avg||0}),rates=T.map(function(x){return x.pass_rate||0});
      var a0=Math.min.apply(null,avgs.concat([5])),a1=Math.max.apply(null,avgs.concat([9]));
      var r0=Math.min.apply(null,rates.concat([55])),r1=Math.max.apply(null,rates.concat([95]));
      var ta=document.getElementById('trend-avg');if(ta)ta.setAttribute('points',mapPts(avgs,a0,a1));
      var tr=document.getElementById('trend-rate');if(tr)tr.setAttribute('points',mapPts(rates,r0,r1));
    });
  }
  var scoreSel=document.querySelector('#pg-scores .card select');
  if(scoreSel)scoreSel.onchange=function(){
    scoreDays=[1,3,7][scoreSel.selectedIndex]||1;loadScores();
  };
  window.cleanupScores=function(btn){
    api.kpost('/api/k/action/cleanup',{days:30}).then(function(r){toast(r.ok!==false?('已清理过期 '+r.removed+' 条'):(r.error||'操作被拒——需要在配置·方案K里设看板令牌'),r.ok===false)});
  };
  window.reflectNow=function(){
    api.kpost('/api/k/action/reflect',{}).then(function(r){toast(r.ok!==false?'已触发反思（调一次 32B，稍后看经验条目）':(r.error||'操作被拒'),r.ok===false)});
  };

  /* =============== 14) 她的日常接真 =============== */
  function paintDaily(){
    if(!$('#pg-daily').classList.contains('act'))return;
    api.kget('/api/k/budget').then(function(r){
      var card=$('#pg-daily .card');if(!card)return;
      var big=card.querySelector('span[style*="26px"]')||card.querySelector('div span');
      var bar=card.querySelector('.bar i');
      if(off(r)){
        if(big)big.textContent='¥—';
        var subs0=card.querySelectorAll('p.sub');if(subs0[0])subs0[0].textContent='她还没上线，启动后开始记账';
        if(bar)bar.style.width='0%';
        return;
      }
      if(big)big.textContent='¥'+r.used;
      // 真机实锤：大字行的 "/ ¥30 · 数据源…" 与「已限流」徽章是原型静态残留——一并接真
      var bigRow=big&&big.parentElement;
      if(bigRow){
        var sps=bigRow.querySelectorAll('.sub');
        if(sps[0])sps[0].textContent='/ '+(r.daily>0?'¥'+r.daily:'不限')+' · 今日实时';
        var bdg=bigRow.querySelector('.bdg');
        if(bdg){var t=(r.tier||'');
          if(t==='ok'){bdg.textContent='余量充足';bdg.className='bdg g';}
          else if(t==='warning'){bdg.textContent='已限流（≥80%）';bdg.className='bdg y';}
          else if(t==='full'){bdg.textContent='已打满熔断';bdg.className='bdg r';}
          else{bdg.textContent='未设预算';bdg.className='bdg';}}
      }
      var subs=card.querySelectorAll('p.sub');
      var pct=r.daily>0?Math.min(100,r.used/r.daily*100):0;
      if(bar)bar.style.width=pct+'%';
      if(subs[0])subs[0].textContent='¥'+r.used+' / '+(r.daily>0?'¥'+r.daily:'不限')+' · '+(r.tier==='ok'?'余量充足':(r.tier==='unlimited'?'未设预算':(r.tier==='warning'?'已限流（≥80%）':'已打满熔断')))+' · 输入 '+(r.in_tokens/1000).toFixed(1)+'k tok · 输出 '+(r.out_tokens/1000).toFixed(1)+'k tok';
    });
    api.kget('/api/k/purpose').then(function(r){
      var card=document.querySelectorAll('#pg-daily .row2 .card')[1];
      if(!card)return;
      if(off(r)){var p0=card.querySelector('p.sub');if(p0)p0.textContent='她还没上线，惦记簿启动后开始记';return}
      var p=card.querySelector('p.sub');
      if(p)p.innerHTML='今日带目的出门 <b style="color:var(--txt)">'+(r.count_today||0)+'/'+(r.daily_max||0)+'</b> · 纯冲浪降级 '+(r.browse_today||0)+'/'+(r.browse_max||0)+' · 收尾冷却 '+(r.closure_cooldown_min||0)+' 分钟';
      var rows=(r.pending||[]).map(function(x){
        return '<div class="cfgline"><span class="bdg '+(x.source==='收尾惦记'?'y':'g')+'">'+esc(x.source)+'</span><div style="flex:1;font-size:13px">'+esc(x.summary)+(x.next_opening?' · 接法：'+esc(x.next_opening):'')+'</div><span class="sub" style="font-size:11px">剩'+x.remain_days+'天</span></div>';
      }).join('');
      var hist=(r.history||[]).map(function(h){return '· '+h.time+' <span class="bdg g">'+esc(h.outcome)+'</span> '+esc(h.summary)}).join(' ');
      var first=card.querySelectorAll('p.sub');
      if(first[2])first[2].innerHTML='出门流水：';
      if(first[3])first[3].innerHTML=hist||'今天还没出门';
      var old=card.querySelectorAll('.cfgline');
      old.forEach(function(el,i){if(i<old.length){el.remove()}});
      var anchor=card.querySelector('p.sub');
      if(anchor&&rows)anchor.insertAdjacentHTML('afterend',rows);
    });
    api.kget('/api/k/mood_curve?lines=120').then(function(r){
      var ev=document.getElementById('ecg-v'),ea=document.getElementById('ecg-a'),now=document.getElementById('ecg-now');
      if(off(r)){
        if(ev)ev.setAttribute('points','');if(ea)ea.setAttribute('points','');
        if(now)now.textContent='';
        var wrap=ev&&ev.parentElement&&ev.parentElement.parentElement;
        var sub=wrap&&wrap.querySelector('p.sub');if(sub)sub.textContent='她还没上线，启动后心电图开始跳';
        return;
      }
      var S=r.series||[];
      var X=function(i){return i*(560/Math.max(1,S.length-1))};
      var pv=S.map(function(p,i){return X(i).toFixed(1)+','+(75-Math.max(-1,Math.min(1,p.v))*66).toFixed(1)}).join(' ');
      var pa=S.map(function(p,i){return X(i).toFixed(1)+','+(143-Math.max(0,Math.min(1,p.a))*132).toFixed(1)}).join(' ');
      if(ev)ev.setAttribute('points',pv);
      if(ea)ea.setAttribute('points',pa);
      if(now&&S.length){var L=S[S.length-1];now.textContent='V'+(L.v>=0?'+':'')+L.v.toFixed(2)+' / A+'+L.a.toFixed(2);}
      // 真机实锤：原型静态「理由：下午和熟人聊得开心」残留——用真 mood.reason 重建整行（保留 ecg-now 挂钩）
      var nowWrap=now&&now.parentElement;
      if(nowWrap){
        nowWrap.innerHTML='当前 <b style="color:var(--pink)" id="ecg-now"></b> · 理由：'+esc(window.__moodReason||'她没说为什么');
        var nn=document.getElementById('ecg-now');
        if(nn&&S.length){var L2=S[S.length-1];nn.textContent='V'+(L2.v>=0?'+':'')+L2.v.toFixed(2)+' / A+'+L2.a.toFixed(2);}
      }
    });
    api.kget('/api/k/schedule').then(function(r){
      var cards=document.querySelectorAll('#pg-daily .row2');
      var card=cards[2]&&cards[2].querySelectorAll('.card')[0];
      if(!card)return;
      if(off(r)){
        var h30=card.querySelector('h3');if(h30)h30.innerHTML='今日日程<span class="bdg" style="margin-left:6px">她还没上线</span>';
        var ol0=card.querySelector('ol');if(ol0)ol0.innerHTML='<li class="sub">她还没上线，启动后会按人设自动排日程</li>';
        return;
      }
      var h3=card.querySelector('h3');
      if(h3)h3.innerHTML='今日日程（按人设自动生成）<span class="bdg '+(r.enabled?'g':'')+'" style="margin-left:6px">'+(r.enabled?'已启用':'未启用')+'</span>';
      var ol=card.querySelector('ol');
      if(ol)ol.innerHTML=(r.tasks||[]).length?r.tasks.map(function(t){return '<li>'+esc(t)+'</li>'}).join(''):'<li class="sub">今天还没有日程——她醒来后会自己安排</li>';
    });
    api.kget('/api/k/modules_status').then(function(r){
      var cards=document.querySelectorAll('#pg-daily .row2');
      var card=cards[2]&&cards[2].querySelectorAll('.card')[1];
      if(!card)return;
      var grid=card.querySelector('div[style*="grid"]');
      if(off(r)){if(grid)grid.innerHTML='<p class="sub">她还没上线，模块脉搏启动后开始跳</p>';return}
      if(grid)grid.innerHTML=(r.modules||[]).slice(0,8).map(function(m){
        var dt=Date.now()/1000-m.last_active;
        var st=dt<300?['g','活跃']:(dt<3600?['y','待机']:['','沉睡']);
        return '<div style="background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer" onclick="go(\'log\')"><b style="font-size:13px">'+esc(m.module)+' <span class="bdg '+st[0]+'" style="margin-left:4px">'+st[1]+'</span></b><p class="sub" style="font-size:11.5px">'+ago(m.last_active)+' · '+m.size_kb+' KB</p></div>';
      }).join('')||'<p class="sub">还没有模块日志</p>';
    });
    api.kget('/api/k/memos').then(function(r){paintMemos(r)});
    api.kget('/api/k/sessions').then(function(r){paintBurst(r)});
  }
  function paintMemos(r){
    var box=document.querySelectorAll('#pg-daily .row2')[1];
    if(!box)return;
    var card=box.querySelectorAll('.card')[1];
    if(!card)return;
    var memoHost=card.querySelectorAll(':scope > .card')[0];
    if(off(r)){if(memoHost)memoHost.innerHTML='<p class="sub">她还没上线</p>';return}
    var D=r.data||{};
    var keys=Object.keys(D);
    if(memoHost){
      if(!keys.length){memoHost.innerHTML='<p class="sub">当前没有交接 memo——她话题断了且值得接的时候才会写</p>';}
      else{var m=D[keys[0]]||{};
        memoHost.innerHTML='<div style="display:flex;gap:8px;align-items:center"><b style="font-size:13px">'+esc(chatName(keys[0]))+'</b><span class="bdg g">'+esc(m.status||'?')+'</span></div>'+
          '<p style="font-size:13px;margin-top:6px">'+esc(m.text||m.content||JSON.stringify(m).slice(0,120))+'</p>';}
    }
  }
  function paintBurst(r){
    var box=document.querySelectorAll('#pg-daily .row2')[1];
    if(!box)return;
    var card=box.querySelectorAll('.card')[1];
    if(!card)return;
    var burst=card.querySelectorAll(':scope > .card')[1];
    if(off(r)){if(burst)burst.innerHTML='<p class="sub">她还没上线</p>';return}
    var S=r.data||[];
    if(burst)burst.innerHTML=S.length?('<div style="display:flex;gap:8px;align-items:center"><b style="font-size:13px">'+esc(chatName(S[0].stream_id))+'</b><span class="bdg">'+S[0].turns+' 轮</span><span class="sub" style="margin-left:auto">约 '+(S[0].est_tokens/1000).toFixed(1)+'k tok</span></div>'+
      '<p class="sub" style="margin-top:6px">'+esc(String(S[0].tail||'').slice(0,60))+' · 最后活跃 '+ago(S[0].last_active)+(S[0].compacting?' · 压缩中':'')+'</p>')
      :'<p class="sub">没有进行中的突发会话（热聊时白名单群自动开）</p>';
  }
  window.dbgMemo=function(){
    var sid=(document.getElementById('dbg-sid')||{}).value||'';
    var text=(document.getElementById('dbg-txt')||{}).value||'';
    if(!sid||!text){toast('stream_id 和内容都要填喵',true);return}
    api.kpost('/api/k/action/memo',{stream_id:sid,text:text}).then(function(r){toast(r.msg||r.error||'',r.ok===false)});
  };

  /* =============== 15) 报错与账单接真 =============== */
  function paintBill(){
    if(!$('#pg-bill').classList.contains('act'))return;
    api.kget('/api/k/errors').then(function(r){
      var box=$('#pg-bill .row2 .card table');
      if(!box)return;
      if(off(r)){box.innerHTML='<tr><td class="sub">她还没上线</td></tr>';return}
      box.innerHTML='<tr><th>模块</th><th>内容</th></tr>'+((r.errors||[]).slice(-12).reverse().map(function(e){
        return '<tr><td style="width:110px">'+esc(e.module)+'</td><td class="sub">'+esc(e.line)+'</td></tr>';
      }).join('')||'<tr><td colspan="2" class="sub">各模块日志尾部无报错行，很健康</td></tr>');
    });
    api.kget('/api/k/bills?days=7').then(function(r){
      var cards=document.querySelectorAll('#pg-bill .card');
      var live=cards[1];                       // 今日实时账本（row2 第二张）
      var tbl=cards[2]&&cards[2].querySelector('table');
      var h3=cards[2]&&cards[2].querySelectorAll('h3')[1];
      var bars=cards[2]?cards[2].querySelectorAll('.hbar .fi'):[];
      function clearBars(){
        bars.forEach(function(b){b.style.width='0%';b.textContent='';});
        var lbs=cards[2]?cards[2].querySelectorAll('.hbar .lb'):[];
        lbs.forEach(function(l,i){l.textContent=i===0?'还没有模型花销':'';});
      }
      function clearLive(){
        if(!live)return;
        var lb=live.querySelector('.bar i');if(lb)lb.style.width='0%';
        var sub=live.querySelector('p.sub');
        // 真机实锤：她在线但今日零花销时，"她还没上线"文案误导——按真实状态分叉
        if(sub)sub.textContent=(window.__lastBotSt==='running')
          ?'今天还没有花销——她一开口调用模型，这里就开始记账'
          :'她还没上线，花销账本启动后开始记';
        var hbs=live.querySelectorAll('.hbar');hbs.forEach(function(h){h.style.display='none'});
      }
      if(off(r)){
        if(tbl)tbl.innerHTML='<tr><td colspan="4" class="sub">她还没上线，花销账本启动后开始记</td></tr>';
        if(h3)h3.textContent='Token 账单';
        clearLive();clearBars();
        return;
      }
      if(tbl)tbl.innerHTML='<tr><th>日期</th><th>成本</th><th>请求数</th><th>tokens</th></tr>'+
        ((r.by_day||[]).slice().reverse().map(function(d){
          return '<tr><td>'+esc(d.day)+'</td><td>¥'+d.cost+'</td><td>'+d.n+'</td><td>'+(d.tokens/1000).toFixed(1)+'k</td></tr>';
        }).join('')||'<tr><td colspan="4" class="sub">还没有账单</td></tr>');
      if(h3)h3.textContent='Token 账单（近 '+r.days+' 天 · 共 ¥'+r.total+'）';
      // 按模型成本：有真数据覆写，没数据把原型假横条清掉
      var bm=(r.by_model||[]);
      if(bm.length){
        var mx=Math.max(0.01,bm.reduce(function(m,x){return Math.max(m,x[1])},0));
        var colors=['var(--green)','var(--purple)','var(--cyan)'];
        bm.slice(0,3).forEach(function(x,i){
          if(bars[i]){var lb=bars[i].closest('.hbar').querySelector('.lb');if(lb)lb.textContent=x[0];
            bars[i].style.width=(x[1]/mx*100)+'%';bars[i].style.background=colors[i];bars[i].textContent='¥'+x[1];}
        });
      }else clearBars();
      // 今日实时账本：by_type 接真
      if(live){
        var hbs=live.querySelectorAll('.hbar');
        var bt=(r.by_type||[]);
        var tot=bt.reduce(function(s,x){return s+(+x[1]||0)},0);
        if(bt.length){
          hbs.forEach(function(h){h.style.display=''});
          var lbar=live.querySelector('.bar i');
          if(lbar)lbar.style.width=(r.daily>0?Math.min(100,tot/r.daily*100):(tot>0?8:0))+'%';
          var lsub=live.querySelector('p.sub');
          if(lsub)lsub.innerHTML='¥'+tot.toFixed(2)+' 今日 · <span class="bdg '+(r.daily>0&&tot/r.daily>=0.8?'y':'g')+'">'+(r.daily>0?(tot/r.daily>=0.8?'接近限额':'余量正常'):'未设预算')+'</span> · 历史对账以下表为准';
          var mx2=Math.max(0.01,bt.reduce(function(m,x){return Math.max(m,+x[1]||0)},0));
          bt.slice(0,hbs.length).forEach(function(x,i){
            var lb=hbs[i].querySelector('.lb');var fi=hbs[i].querySelector('.fi');
            if(lb)lb.textContent=x[0];
            if(fi){fi.style.width=((+x[1]||0)/mx2*100)+'%';fi.textContent='¥'+(+x[1]||0).toFixed(1);}
          });
        }else clearLive();
      }
    });
  }

  /* =============== 16) 人设页接真 =============== */
  var TAGSEL=[];
  function paintPersona(){
    if(!$('#pg-pers'))return;
    api.persona_get().then(function(p){
      if(off(p)){
        var pg0=document.getElementById('pg-pers');
        if(pg0)pg0.innerHTML='<div class="card"><p class="sub">人设读取失败：'+esc(p.error||'未知原因')+'（人设存在 bot_config.toml，与上线状态无关，通常是文件被占用）</p></div>';
        return;
      }
      TAGSEL=(p.sides||[]).slice();
      var pg=document.getElementById('pg-pers');
      pg.innerHTML='<div class="card" style="margin-bottom:14px"><p class="sub" style="margin:0">这里和「配置 → 人设与身份」编辑的是<b style="color:var(--txt)">同一份人设数据</b>（bot_config.toml），任一处保存即生效。</p></div>'+
        '<div class="row2"><div class="card"><h3>基本</h3>'+
        '<p class="sub" style="margin-bottom:6px">名字</p><input id="ps-name" value="'+esc(p.nickname)+'">'+
        '<p class="sub" style="margin:12px 0 6px">别名（听到会答应）</p><input id="ps-alias" value="'+esc(p.alias||'')+'" placeholder="逗号分隔，如：麦叠、牢麦">'+
        '<p class="sub" style="margin:12px 0 6px">身份特点（分号分隔多条）</p><input id="ps-identity" value="'+esc(p.identity||'')+'">'+
        '<div style="display:flex;gap:10px;margin-top:12px"><div style="flex:1"><p class="sub" style="margin-bottom:6px">年龄</p><input id="ps-age" value="'+esc(p.age)+'"></div>'+
        '<div style="flex:1"><p class="sub" style="margin-bottom:6px">性别</p><input id="ps-gender" value="'+esc(p.gender)+'"></div></div>'+
        '<p class="sub" style="margin:12px 0 6px">外貌</p><input id="ps-appearance" value="'+esc(p.appearance)+'"></div>'+
        '<div class="card"><h3>说话风格侧面</h3><p class="sub" style="margin-bottom:10px">她人格的细节切面 · 点 × 删除，回车添加</p>'+
        '<div id="ps-sides"></div><div style="display:flex;gap:8px;margin-top:8px"><input id="ps-newside" placeholder="加一条侧面，回车确认" style="flex:1"><button class="btn" onclick="addSide()">加</button></div></div></div>'+
        '<div class="card" style="margin-top:14px"><h3>核心人设</h3>'+
        '<p class="sub" style="margin-bottom:8px">她是谁、怎么来的、在意什么。这段文字是她的"自我"，改动会直接体现在聊天里。</p>'+
        '<textarea id="ps-core" rows="9">'+esc(p.core)+'</textarea>'+
        '<div style="display:flex;gap:10px;margin-top:14px;justify-content:flex-end">'+
        '<button class="btn" onclick="paintPersona()">撤销更改</button>'+
        '<button class="btn primary" onclick="savePersona()">保存人设</button></div></div>';
      // 真机实锤：出厂占位文案直接当数据显示（核心人设空白/侧面5条占位）——给引导提示
      var PLACE='用一句话或几句话描述人格的核心特点';
      var noCore=(!p.core||p.core===PLACE);
      var sides=p.sides||[];
      var noSides=!sides.length||sides.every(function(x){return x==='用一句话或几句话描述人格的一侧细节'});
      if(noCore||noSides){
        pg.insertAdjacentHTML('afterbegin','<div class="card" style="margin-bottom:14px;border-color:var(--yellow)"><p class="sub" style="margin:0"><b style="color:var(--yellow)">她的核心人设还是出厂占位文案</b>——空着等于她还没有灵魂。在下面填上她是谁、说话什么味道，保存后下一轮对话生效。</p></div>');
      }
      renderSides();
      var ns=document.getElementById('ps-newside');
      if(ns)ns.onkeydown=function(e){if(e.key==='Enter')addSide()};
    });
  }
  function renderSides(){
    var box=document.getElementById('ps-sides');if(!box)return;
    box.innerHTML=TAGSEL.map(function(t,i){
      return '<span class="tag on" style="margin:0 8px 8px 0;padding:6px 14px;cursor:pointer" onclick="delSide('+i+')">'+esc(t)+' ×</span>';
    }).join('')||'<p class="sub">还没有侧面条目</p>';
  }
  window.addSide=function(){var v=(document.getElementById('ps-newside')||{}).value||'';v=v.trim();if(!v)return;TAGSEL.push(v);renderSides();var n=document.getElementById('ps-newside');if(n)n.value=''};
  window.delSide=function(i){TAGSEL.splice(i,1);renderSides()};
  window.savePersona=function(){
    api.persona_save({
      nickname:(document.getElementById('ps-name')||{}).value||'',
      core:(document.getElementById('ps-core')||{}).value||'',
      sides:TAGSEL,
      identity:(document.getElementById('ps-identity')||{}).value||'',
      age:(document.getElementById('ps-age')||{}).value||'',
      gender:(document.getElementById('ps-gender')||{}).value||'',
      appearance:(document.getElementById('ps-appearance')||{}).value||''
    }).then(function(r){toast(r.msg||r.error||'',r.ok===false);if(r.ok!==false)refreshCfgIdentity()});
  };

  /* =============== 17) 配置中心接真 =============== */
  // 每组映射：控件序号 → [file, section, key, kind]；null=跳过该控件；特判 kind 见 _literal/前端 price/pair
  var CFG_PLAN=[
    /*0 基础bot*/ [['bot','bot','qq','int'],['bot','bot','nickname','str'],['bot','bot','alias_names','strlist']],
    /*1 服务商*/ 'providers',
    /*2 群组*/ [['bot','groups','talk_allowed','intlist'],['bot','groups','talk_frequency_down','intlist'],['bot','groups','ban_user_id','intlist']],
    /*3 人设身份*/ [['bot','personality','personality_core','str'],['bot','personality','personality_sides','strlist'],['bot','identity','identity_detail','strlist'],['bot','identity','age','int'],['bot','identity','gender','str'],['bot','identity','appearance','str']],
    /*4 日程*/ [['bot','schedule','enable_schedule_gen','bool'],['bot','schedule','enable_schedule_interaction','bool'],['bot','schedule','prompt_schedule_gen','str'],['bot','schedule','time_zone','str']],
    /*5 聊天通用*/ [['bot','chat','allow_focus_mode','bool'],['bot','chat','base_normal_chat_num','int'],['bot','chat','base_focused_chat_num','int'],['bot','chat','observation_context_size','int'],['bot','chat','message_buffer','bool'],['bot','chat','ban_words','strlist']],
    /*6 回复意愿*/ [['bot','normal_chat','willing_mode','str'],null,['bot','normal_chat','emoji_response_penalty','int'],['bot','normal_chat','emoji_chance','float'],['bot','normal_chat','response_willing_amplifier','int'],['bot','normal_chat','mentioned_bot_inevitable_reply','bool'],['bot','normal_chat','at_bot_inevitable_reply','bool']],
    /*7 专注*/ [['bot','focus_chat','reply_trigger_threshold','float'],['bot','focus_chat','default_decay_rate_per_second','float'],['bot','focus_chat','consecutive_no_reply_threshold','int']],
    /*8 统一大脑*/ [['bot','experimental','group_unified_brain','bool'],['bot','experimental','burst_session','bool'],['bot','experimental','burst_idle_min','int'],['bot','experimental','burst_max_tokens','int'],null],
    /*9 TTS*/ [['bot','tts','enable','bool'],['bot','tts','voice_uri','str']],
    /*10 表情包*/ [['bot','emoji','max_emoji_num','int'],['bot','emoji','steal_emoji','bool'],['bot','emoji','save_pic','bool'],['bot','emoji','save_emoji','bool'],['bot','emoji','enable_check','bool'],['bot','emoji','check_prompt','str']],
    /*11 记忆*/ [['bot','memory','build_memory_interval','int'],['bot','memory','forget_memory_interval','int'],['bot','memory','memory_forget_time','int'],['bot','memory','memory_compress_rate','float']],
    /*12 情绪*/ [['bot','mood','mood_update_interval','float'],['bot','mood','mood_decay_rate','float'],['bot','mood','mood_intensity_factor','float']],
    /*13 方案K*/ [['bot','plastic_memory','enable','bool'],['bot','plastic_memory','group_review_enabled','bool'],['bot','plastic_memory','group_review_hour','int'],['bot','plastic_memory','reflection_enabled','bool'],['bot','plastic_memory','reflection_hour','int'],['bot','plastic_memory','score_retention_days','int'],['handoff'],['bot','plastic_memory','dashboard_token','str']],
    /*14 文本风格*/ [['bot','chinese_typo','enable','bool'],['bot','chinese_typo','error_rate','float'],['bot','response_splitter','enable_response_splitter','bool'],['bot','response_splitter','response_max_length','int'],['bot','response_splitter','enable_kaomoji_protection','bool'],null],
    /*15 实验性*/ [['bot','experimental','enable_friend_chat','bool'],['bot','experimental','pfc_chatting','bool'],['bot','remote','enable','bool']],
    /*16 运行时env*/ [['env','','SIMPLE_OUTPUT','bool'],['env','','CONSOLE_LOG_LEVEL','envstr'],['env','','FILE_LOG_LEVEL','envstr']],
    /*17 适配器*/ [['adapter','Voice','use_tts','bool'],['adapter','Debug','level','envstr']],
    /*18 lpmm*/ ['sfkey',['lpmm','entity_extract.llm','model','envstr'],['lpmm','rdf_build.llm','model','envstr'],['lpmm','qa.llm','model','envstr'],['lpmm','embedding','model','envstr'],['lpmm','embedding','dimension','int'],['pair','qa.params','relation_search_top_k','int'],null],
    /*19 模型调度*/ [
      ['bot','model.llm_reasoning','name','envstr'],['price','model.llm_reasoning'],
      ['bot','model.llm_normal','name','envstr'],['price','model.llm_normal'],['bot','model.llm_normal','temp','float'],
      ['bot','model.llm_sub_heartflow','name','envstr'],['price','model.llm_sub_heartflow'],['bot','model.llm_sub_heartflow','temp','float'],
      ['bot','model.llm_plan','name','envstr'],['price','model.llm_plan'],
      ['bot','model.llm_heartflow','name','envstr'],['price','model.llm_heartflow'],
      ['bot','model.llm_topic_judge','name','envstr'],['price','model.llm_topic_judge'],
      ['bot','model.llm_summary','name','envstr'],['price','model.llm_summary'],
      ['bot','model.vlm','name','envstr'],['price','model.vlm'],
      ['bot','model.llm_observation','name','envstr'],['price','model.llm_observation'],
      ['bot','model.llm_PFC_action_planner','name','envstr'],['price','model.llm_PFC_action_planner'],
      null],
    /*20 预算*/ [['bot','token_budget','daily_cny','float']]
  ];
  function ctrlsOf(sec){var card=sec.querySelector('.card');return card?Array.from(card.querySelectorAll('input:not([type=button]),select')):[]}
  function getPath(data,section,key){
    var node=data;
    if(section){var parts=section.split('.');for(var i=0;i<parts.length;i++){node=(node||{})[parts[i]];if(node==null)return undefined}}
    return node==null?undefined:node[key];
  }
  function setCtl(ctl,val,kind){
    if(!ctl)return;
    if(ctl.type==='checkbox'){ctl.checked=(val===true||val==='true'||val===1)}
    else if(ctl.tagName==='SELECT'&&kind!=='envstr'){
      for(var i=0;i<ctl.options.length;i++){if(String(ctl.options[i].value||ctl.options[i].text).indexOf(String(val))>=0){ctl.selectedIndex=i;break}}
    }
    else if(kind==='strlist'&&Array.isArray(val)){ctl.value=val.join(', ')}
    else if(val!=null){ctl.value=val}
  }
  function fillCfg(){
    api.cfg_get().then(function(r){
      if(off(r)){return}
      window.__cfgData=r.data||{};
      var secs=document.querySelectorAll('#cfg-acc details.cfgsec');
      secs.forEach(function(sec,si){
        var plan=CFG_PLAN[si];
        if(!plan||plan==='providers'||plan==='sfkey')return;
        var cts=ctrlsOf(sec);
        plan.forEach(function(p,pi){
          if(!p)return;
          if(p[0]==='handoff'||p[0]==='price'||p[0]==='pair')return;
          var ctl=cts[pi];
          if(!ctl)return;
          ctl.dataset.cfg=JSON.stringify(p);
          setCtl(ctl,getPath(window.__cfgData,p[1],p[2]),p[3]);
        });
      });
      // env / adapter / lpmm 单独填充
      api.cfg_file('env').then(function(e){
        if(off(e))return;
        var env={};e.text.split('\n').forEach(function(ln){var m=ln.match(/^([A-Z_0-9]+)=(.*)$/);if(m)env[m[1]]=m[2]});
        window.__envData=env;
        secs.forEach(function(sec,si){
          var plan=CFG_PLAN[si];if(!plan||plan==='providers'||plan==='sfkey')return;
          var cts=ctrlsOf(sec);
          plan.forEach(function(p,pi){
            if(!p||p[0]!=='env')return;
            var ctl=cts[pi];if(!ctl)return;
            setCtl(ctl,env[p[2]],p[3]==='bool'?'bool':'envstr');
            if(ctl.type!=='checkbox')ctl.dataset.env=p[2];
          });
        });
        // 服务商卡 Key 回显：读真 .env，有值显示掩码，无值清空（原型写死的假点串不可信）
        var provSec=null;
        secs.forEach(function(sec,si){if(CFG_PLAN[si]==='providers')provSec=sec;});
        if(provSec){
          provSec.querySelectorAll('.card').forEach(function(c,ci){
            var kname=ci===0?'SILICONFLOW_KEY':(ci===1?'DEEP_SEEK_KEY':'');
            if(!kname)return;
            var ins=c.querySelectorAll('input');var k=ins[2];if(!k)return;
            var v=String(env[kname]||'').trim();
            k.dataset.masked='';
            if(!v||v==='sk-missing-please-set'){
              k.value='';k.placeholder='未配置——填入 Key 后点「保存·自动重启」';
            }else{
              k.value=v.slice(0,5)+'••••••••'+v.slice(-4);
              k.dataset.masked='1';
              k.onfocus=function(){if(k.dataset.masked){k.dataset.masked='';k.value='';k.placeholder='输入新 Key（留空=不改动）'}};
            }
          });
        }
      });
      api.cfg_file('adapter').then(function(e){ if(!off(e))window.__adapterText=e.text; });
    });
  }
  function collect(sec){
    var si=Array.from(document.querySelectorAll('#cfg-acc details.cfgsec')).indexOf(sec);
    var plan=CFG_PLAN[si];
    if(!plan)return null;
    if(plan==='providers'){
      var cards=Array.from(sec.querySelectorAll('.card')).filter(function(c){return c.querySelector('.cfgline')});
      return {providers:cards.map(function(c){var ins=c.querySelectorAll('input');var kv=ins[2]?ins[2].value:'';if(ins[2]&&ins[2].dataset.masked)kv='';return {name:ins[0]?ins[0].value:'',url:ins[1]?ins[1].value:'',key:kv}}).filter(function(x){return x.url||x.key})};
    }
    var cts=ctrlsOf(sec),ops=[];
    plan.forEach(function(p,pi){
      if(!p)return;
      var ctl=cts[pi];if(!ctl)return;
      if(p[0]==='handoff'){var m=String(ctl.value).match(/(\d+)\s*-\s*(\d+)/);
        if(m){ops.push({file:'bot',section:'plastic_memory',key:'handoff_min_gap_min',kind:'int',value:m[1]});
              ops.push({file:'bot',section:'plastic_memory',key:'handoff_max_gap_min',kind:'int',value:m[2]})}return}
      if(p[0]==='price'){var mm=String(ctl.value).match(/([\d.]+)\s*\/\s*([\d.]+)/);
        var vin=mm?mm[1]:0,vout=mm?mm[2]:0;
        ops.push({file:'bot',section:p[1],key:'pri_in',kind:'float',value:vin});
        ops.push({file:'bot',section:p[1],key:'pri_out',kind:'float',value:vout});return}
      if(p[0]==='pair'){var mp=String(ctl.value).match(/([\d.]+)\s*\/\s*([\d.]+)/);
        if(mp){ops.push({file:p[1]==='qa.params'?'lpmm':'bot',section:p[1],key:p[2],kind:'int',value:mp[1]})}return}
      if(p[0]==='sfkey'){return}
      var v=ctl.type==='checkbox'?ctl.checked:ctl.value;
      ops.push({file:p[0],section:p[1],key:p[2],kind:p[3],value:v});
    });
    return {ops:ops};
  }
  // 配置/人设保存成功后刷新总览身份卡（昵称/头像字母/QQ 立即跟随，不等下次轮询）
  function refreshCfgIdentity(){
    api.cfg_get().then(function(r){
      if(!off(r)){window.__cfg=(r.data||{}).bot||{};window.__botQQ=(window.__cfg||{}).qq;}
      paintHome(null,null,null);
    });
  }
  window.saveCfgSec=function(btn,hot){
    var sec=btn.closest('details.cfgsec');if(!sec){toast('找不到配置组',true);return}
    var data=collect(sec);
    if(!data){toast('该组暂未接线',true);return}
    if(data.providers){
      api.providers_save(data.providers).then(function(r){toast(r.msg||r.error||'',r.ok===false)});return;
    }
    if(!data.ops.length){toast('这组没有可保存的项',true);return}
    var needRestart=data.ops.some(function(o){return o.file!=='env'});
    data.restart='auto';
    api.cfg_save(data).then(function(r){
      if(r.ok===false){toast(r.error||'保存失败',true);return}
      toast(r.msg||'已保存');
      refreshCfgIdentity();
      fillCfg();
    });
  };
  window.cfgFile=function(el,id){
    el.parentElement.querySelectorAll('.tag').forEach(function(t){t.classList.remove('on')});
    el.classList.add('on');
    var map={f1:'bot',f2:'lpmm',f3:'adapter',f4:'env'};
    ['f1','f2','f3','f4'].forEach(function(f){var n=document.getElementById('cf-'+f);if(n)n.style.display=f===id?'':'none'});
    api.cfg_file(map[id]).then(function(r){
      var n=document.getElementById('cf-'+id);
      if(n&&r&&r.ok!==false)n.textContent=r.text;
    });
  };
  window.saveAllCfg=function(){
    if(!confirm('将保存所有组的修改。\n需要重启的组会自动执行：优雅停止全家桶 → 写入 → 重新拉起（约 30 秒）。\n继续吗？'))return;
    var secs=document.querySelectorAll('#cfg-acc details.cfgsec');
    var ops=[];
    secs.forEach(function(sec){
      var d=collect(sec);
      if(d&&d.ops)ops=ops.concat(d.ops);
      if(d&&d.providers){api.providers_save(d.providers)}
    });
    if(!ops.length){toast('没有可保存的项',true);return}
    api.cfg_save({ops:ops,restart:'auto'}).then(function(r){toast(r.msg||r.error||'',r.ok===false);refreshCfgIdentity();fillCfg()});
  };
  var allBtn=document.querySelector('#pg-cfg .card .btn.primary');
  document.querySelectorAll('#pg-cfg > div:last-child button').forEach(function(b){
    if(b.textContent.indexOf('保存全部')>=0)b.onclick=saveAllCfg;
    if(b.textContent.indexOf('恢复默认')>=0)b.onclick=function(){toast('恢复默认会覆盖你的全部个性化配置，太危险，这个按钮没接——需要时去 template 目录手动比对',true)};
  });

  /* =============== 18) 首启向导接真 =============== */
  window.wizTest=function(){
    var r=$('#w-test-r');var key=($('#w-key')||{}).value||'';var url=($('#w-baseurl')||{}).value||'https://api.siliconflow.cn/v1/';
    r.textContent='连接中…';r.style.color='var(--sub)';$('#w-test').disabled=true;
    api.test_provider(url,key).then(function(res){
      r.textContent=res.ok!==false?res.msg:res.error;
      r.style.color=res.ok!==false?'var(--green)':'var(--red)';
      $('#w-test').disabled=false;
    });
  };
  window.wizFinish=function(){
    var qq=($('#w-qq')||{}).value||'';
    var key=($('#w-key')||{}).value||'';
    var burl=($('#w-baseurl')||{}).value||'';
    var dsk=($('#w-key-ds')||{}).value||'';
    var perSel=document.querySelector('[data-s="3"] .opt-card.sel b');
    var styleMap={'傲娇系':'tsundere','温柔系':'gentle','元气系':'genki'};
    var style=styleMap[(perSel||{}).textContent]||'tsundere';
    var tgs={emoji:document.querySelectorAll('#w-toggles input')[0]?document.querySelectorAll('#w-toggles input')[0].checked:'1'};
    api.wizard_save(qq,key,burl,dsk,style,tgs).then(function(r){
      if(r.ok===false){toast(r.error||'写入失败',true);return}
      toast('配置已保存，全家桶启动中…');
      api.finish_wizard();
      setTimeout(function(){toast('她上线了，先去群里打个招呼吧');},2500);
      var w=document.getElementById('wizard');if(w)w.style.display='none';
    });
  };
  window.wizReplay=function(){wizGo(0);var w=document.getElementById('wizard');if(w)w.style.display='flex'};

  /* =============== 19) 页面切换懒加载 =============== */
  var _go=window.go;
  window.go=function(id){
    _go(id);
    try{
      if(id==='home')pollHome();
      else if(id==='chat'){loadChats();}   // 真机实锤：每次进页都刷——聊天后回来记录不更新（原逻辑只在 CHATS 空时拉一次）
      else if(id==='rel')loadRels();
      else if(id==='mem')memTab('graph',document.querySelector('[data-mt="graph"]'));
      else if(id==='scores')loadScores();
      else if(id==='daily')paintDaily();
      else if(id==='bill')paintBill();
      else if(id==='pers')paintPersona();
      else if(id==='cfg')fillCfg();
    }catch(e){}
  };
  // boot 预取：QQ号 + 会话名表
  api.cfg_get().then(function(r){
    if(!off(r)){window.__cfg=(r.data||{}).bot||{};window.__botQQ=(window.__cfg||{}).qq;}
  });
  api.kget('/api/k/db/chats?limit=30').then(function(r){
    if(!off(r))CHATS=r.items||[];
    renderConvsReal();   // boot 即清掉原型写死的假会话列表（成功=真数据，失败/空=占位）
  });
  // 大脑页原型写死的演示下拉 → 换成说明（过滤真由事件流数据自带）
  var bsel=document.querySelector('#pg-brain select');
  if(bsel){bsel.innerHTML='<option>全部聊天流（自动）</option>';bsel.disabled=true;}
  // 心流页原型下拉（"麦麦开发交流群/演示私聊"演示残留）→ 同样清成说明
  var fsel=document.querySelector('#pg-flow select');
  if(fsel){fsel.innerHTML='<option>全部聊天流（自动）</option>';fsel.disabled=true;}
  // 聊天页激活期间 20s 轻刷当前会话（她正在回话时，记录页跟着滚动）
  setInterval(function(){
    if($('#pg-chat').classList.contains('act')&&curChat&&!$('#pg-chat .chathead input').value){
      api.kget('/api/k/db/messages?chat_id='+encodeURIComponent(curChat)+'&size=60').then(function(r){
        if(!off(r)){chatCache[curChat]=r.items||[];renderMsgs('');}
      });
    }
  },20000);
 }
})();
