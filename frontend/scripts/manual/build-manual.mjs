// 產生單一 HTML 操作手冊：把內容與 Base64 圖片組裝成可離線開啟的檔案。
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { acquisition, cash, contacts, kiosk } from "./content-2.mjs";
import { cover, overview, quickstart } from "./content-1.mjs";
import { campaigns, consignment, inventory, menu, pos, sales } from "./content-3.mjs";
import { backup, purchasing, reports, settings, signing, stocktake } from "./content-4.mjs";
import { admin, coverage, faq, flows } from "./content-5.mjs";

const IMAGES = JSON.parse(readFileSync(join(homedir(), "tmp", "lu-camp-manual", "images.json"), "utf8"));
const OUT = process.argv[2] ?? join(homedir(), "tmp", "lu-camp-manual", "露營二手POS-系統操作手冊.html");

const CHAPTERS = [
  { id: "ch-cover", num: "", title: "手冊首頁", html: cover },
  { id: "ch-quickstart", num: "1", title: "快速開始", html: quickstart },
  { id: "ch-overview", num: "2", title: "系統功能總覽", html: overview },
  { id: "ch-cash", num: "3", title: "現金對帳", html: cash },
  { id: "ch-contacts", num: "4", title: "會員 / 賣方", html: contacts },
  { id: "ch-kiosk", num: "5", title: "顧客螢幕（手持簽署裝置）", html: kiosk },
  { id: "ch-acquisition", num: "6", title: "收購鑑價入庫", html: acquisition },
  { id: "ch-pos", num: "7", title: "POS 結帳", html: pos },
  { id: "ch-sales", num: "8", title: "交易紀錄", html: sales },
  { id: "ch-consignment", num: "9", title: "寄售付款", html: consignment },
  { id: "ch-inventory", num: "10", title: "庫存", html: inventory },
  { id: "ch-menu", num: "11", title: "餐飲菜單", html: menu },
  { id: "ch-campaigns", num: "12", title: "門市活動", html: campaigns },
  { id: "ch-purchasing", num: "13", title: "採購 / 補貨", html: purchasing },
  { id: "ch-stocktake", num: "14", title: "盤點", html: stocktake },
  { id: "ch-signing", num: "15", title: "簽署紀錄", html: signing },
  { id: "ch-reports", num: "16", title: "報表", html: reports },
  { id: "ch-settings", num: "17", title: "設定", html: settings },
  { id: "ch-backup", num: "18", title: "備份與還原", html: backup },
  { id: "ch-flows", num: "19", title: "跨系統完整流程", html: flows },
  { id: "ch-admin", num: "20", title: "管理員功能總表", html: admin },
  { id: "ch-faq", num: "21", title: "常見問題與錯誤排除", html: faq },
  { id: "ch-coverage", num: "22", title: "附錄：功能覆蓋與驗證狀態", html: coverage },
];

// 把 data-img="id" 換成實際 base64；同時收集 h2 供目錄使用。
let missing = 0;
function inlineImages(html) {
  return html.replace(/<img data-img="([^"]+)"([^>]*)\/>/g, (m, id, rest) => {
    const img = IMAGES[id];
    if (!img) {
      missing += 1;
      console.warn(`⚠ 缺圖：${id}`);
      return `<span class="img-missing">（圖片缺漏：${id}）</span>`;
    }
    return `<img src="${img.src}" width="${img.w}" height="${img.h}"${rest}/>`;
  });
}

function subheads(html) {
  const out = [];
  const re = /<h2 id="([^"]+)"[^>]*>([\s\S]*?)<\/h2>/g;
  const matches = [];
  let m;
  while ((m = re.exec(html)) !== null) matches.push(m);
  matches.forEach((mm, i) => {
    const start = mm.index + mm[0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : html.length;
    const body = html
      .slice(start, end)
      .replace(/<img[^>]*>/g, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 900);
    out.push({ id: mm[1], text: mm[2].replace(/<[^>]+>/g, "").trim(), body });
  });
  return out;
}

const toc = CHAPTERS.map((c) => ({ ...c, subs: subheads(c.html) }));

const tocHtml = toc
  .map(
    (c) => `<li class="toc-chapter">
  <a href="#${c.id}" class="toc-link toc-link-chapter" data-target="${c.id}">${c.num ? `<span class="toc-num">${c.num}</span>` : ""}${c.title}</a>
  ${c.subs.length ? `<ul class="toc-subs">${c.subs.map((s) => `<li><a href="#${s.id}" class="toc-link toc-link-sub" data-target="${s.id}">${s.text}</a></li>`).join("")}</ul>` : ""}
</li>`,
  )
  .join("\n");

const bodyHtml = CHAPTERS.map(
  (c) => `<section class="chapter" id="${c.id}" aria-labelledby="${c.id}-h">
  <h1 id="${c.id}-h">${c.num ? `<span class="ch-num">${c.num}</span>` : ""}${c.title}</h1>
  ${inlineImages(c.html)}
  <p class="back-to-top"><a href="#top">↑ 回到頁首</a></p>
</section>`,
).join("\n");

const css = `
:root{
  --bg:#f7f5f0; --panel:#fff; --ink:#23241f; --muted:#5f6157; --line:#e2ded2;
  --brand:#2f5d3a; --brand-soft:#e8f0e6; --warn:#8a5a00; --warn-bg:#fdf4e0;
  --danger:#9b1c1c; --danger-bg:#fdeaea; --info:#1e4f7a; --info-bg:#e9f1f8;
  --pre:#4a4a7a; --pre-bg:#eeeef8;
  --radius:12px; --sidebar:310px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth; scroll-padding-top:72px}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:16px; line-height:1.75; word-break:break-word; overflow-wrap:anywhere;
}
a{color:var(--brand)}
.skip-link{position:absolute;left:-999px;top:0;background:#fff;padding:8px 14px;z-index:100}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.skip-link:focus{left:8px;top:8px}

/* 版面 */
.layout{display:flex; min-height:100vh}
.sidebar{
  width:var(--sidebar); flex:0 0 var(--sidebar); background:var(--panel);
  border-right:1px solid var(--line); position:sticky; top:0; height:100vh; overflow-y:auto; padding:18px 14px 40px;
}
.sidebar h2{font-size:15px; margin:0 0 10px; color:var(--muted); letter-spacing:.04em}
.main{flex:1 1 auto; min-width:0; padding:26px clamp(14px,3vw,44px) 80px; max-width:1180px}
.topbar{
  position:sticky; top:0; z-index:30; background:rgba(247,245,240,.94); backdrop-filter:blur(6px);
  border-bottom:1px solid var(--line); padding:10px clamp(14px,3vw,44px); display:flex; gap:10px; align-items:center;
  margin:-26px clamp(-44px,-3vw,-14px) 22px; flex-wrap:wrap;
}
.topbar .doc-title{font-weight:700; margin-right:auto; font-size:15px}
#menu-toggle{display:none}
.search-box{position:relative; flex:1 1 220px; max-width:340px}
.search-box input{
  width:100%; padding:9px 12px; border:1px solid var(--line); border-radius:999px; background:#fff; font-size:15px; color:inherit;
}
.search-box input:focus{outline:2px solid var(--brand); outline-offset:1px}
#search-results{
  position:absolute; left:0; right:0; top:calc(100% + 6px); background:#fff; border:1px solid var(--line);
  border-radius:10px; box-shadow:0 12px 30px rgba(0,0,0,.12); max-height:60vh; overflow:auto; display:none; z-index:40;
}
#search-results.open{display:block}
#search-results a{display:block; padding:9px 13px; text-decoration:none; color:var(--ink); font-size:14px; border-bottom:1px solid #f0ede4}
#search-results a:last-child{border-bottom:0}
#search-results a:hover,#search-results a:focus{background:var(--brand-soft)}
#search-results .sr-ch{display:block; font-size:12px; color:var(--muted)}
#search-results .sr-empty{padding:12px 13px; color:var(--muted); font-size:14px}

/* 目錄 */
.toc{list-style:none; margin:0; padding:0}
.toc-subs{list-style:none; margin:2px 0 8px; padding:0 0 0 12px; border-left:2px solid #eee9dc}
.toc-link{display:block; padding:6px 9px; border-radius:8px; text-decoration:none; color:var(--ink); font-size:14.5px}
.toc-link-sub{color:var(--muted); font-size:13.5px; padding:4px 9px}
.toc-link:hover{background:var(--brand-soft)}
.toc-link.active{background:var(--brand); color:#fff}
.toc-link-sub.active{background:var(--brand-soft); color:var(--brand); font-weight:600}
.toc-num{display:inline-block; min-width:22px; color:var(--muted); font-variant-numeric:tabular-nums}
.toc-link.active .toc-num{color:#e6efe6}

/* 內容 */
.chapter{background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); padding:clamp(16px,3vw,34px); margin-bottom:26px}
.chapter h1{font-size:clamp(22px,3.4vw,30px); margin:0 0 20px; padding-bottom:12px; border-bottom:3px solid var(--brand); line-height:1.35}
.ch-num{display:inline-block; background:var(--brand); color:#fff; border-radius:8px; padding:1px 11px; margin-right:10px; font-size:.75em; vertical-align:middle}
.chapter h2{font-size:clamp(18px,2.5vw,22px); margin:34px 0 12px; padding-left:11px; border-left:5px solid var(--brand)}
.chapter h3{font-size:17px; margin:22px 0 8px; color:var(--brand)}
.chapter p{margin:10px 0}
code{background:#f0ede4; padding:1px 6px; border-radius:5px; font-size:.92em; font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
kbd{background:#fff; border:1px solid var(--line); border-bottom-width:2px; border-radius:5px; padding:0 6px; font-size:.86em}
.steps{counter-reset:step; list-style:none; padding:0; margin:14px 0}
.steps>li{counter-increment:step; position:relative; padding:8px 0 8px 44px; border-bottom:1px dashed #ece7da}
.steps>li:last-child{border-bottom:0}
.steps>li::before{
  content:counter(step); position:absolute; left:0; top:8px; width:29px; height:29px; border-radius:50%;
  background:var(--brand); color:#fff; display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700;
}
.table-wrap{overflow-x:auto; margin:14px 0; border:1px solid var(--line); border-radius:10px}
table{border-collapse:collapse; width:100%; min-width:460px; font-size:14.5px; background:#fff}
th,td{border-bottom:1px solid var(--line); padding:9px 12px; text-align:left; vertical-align:top}
th{background:#f3f0e8; font-weight:700; white-space:nowrap}
tr:last-child td{border-bottom:0}
.req{color:var(--danger); font-weight:700; white-space:nowrap}
.opt{color:var(--muted); white-space:nowrap}
.meta{margin:12px 0; display:grid; gap:0; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:#fff}
.meta>div{display:grid; grid-template-columns:150px 1fr; border-bottom:1px solid var(--line)}
.meta>div:last-child{border-bottom:0}
.meta dt{background:#f3f0e8; padding:9px 12px; font-weight:700; margin:0}
.meta dd{padding:9px 12px; margin:0}

/* 提示塊 */
.box{border-radius:10px; padding:13px 16px; margin:16px 0; border-left:5px solid}
.box-title{font-weight:700; margin:0 0 6px !important}
.box p:last-child,.box ul:last-child{margin-bottom:0}
.box ul{margin:6px 0; padding-left:22px}
.box-info{background:var(--info-bg); border-color:var(--info)} .box-info .box-title{color:var(--info)}
.box-warn{background:var(--warn-bg); border-color:var(--warn)} .box-warn .box-title{color:var(--warn)}
.box-danger{background:var(--danger-bg); border-color:var(--danger)} .box-danger .box-title{color:var(--danger)}
.box-pre{background:var(--pre-bg); border-color:var(--pre)} .box-pre .box-title{color:var(--pre)}
.tag{display:inline-block; font-size:12.5px; padding:1px 9px; border-radius:999px; margin-left:6px; vertical-align:middle; white-space:nowrap}
.tag-admin{background:#f3e3e3; color:var(--danger)}
.tag-all{background:var(--brand-soft); color:var(--brand)}
.tag-perm{background:#efe9d6; color:var(--warn)}
.tag-info{background:var(--info-bg); color:var(--info)}
.tag-warn{background:var(--warn-bg); color:var(--warn)}
.tag-danger{background:var(--danger-bg); color:var(--danger)}
.tag-pre{background:var(--pre-bg); color:var(--pre)}
.msg{padding:1px 8px; border-radius:6px; font-size:.95em}
.msg-ok{background:var(--brand-soft); color:var(--brand)}
.msg-err{background:var(--danger-bg); color:var(--danger)}

/* 流程圖 */
.flow{display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:16px 0; padding:14px; background:#f3f0e8; border-radius:10px}
.flow-node{background:#fff; border:2px solid var(--brand); color:var(--brand); border-radius:999px; padding:6px 15px; font-size:14.5px; font-weight:600}
.flow-arrow{color:var(--brand); font-weight:700}

/* 截圖 */
.shot{margin:16px 0; padding:0; background:#fff; border:1px solid var(--line); border-radius:10px; overflow:hidden}
.shot img{display:block; width:100%; height:auto; max-width:100%; cursor:zoom-in; background:#fbfaf7}
.shot figcaption{font-size:13.5px; color:var(--muted); padding:9px 13px; background:#faf8f3; border-top:1px solid var(--line)}
.shot-row{display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:16px; margin:16px 0; align-items:start}
.shot-row .shot{margin:0}
.img-missing{display:block; padding:14px; background:var(--danger-bg); color:var(--danger); border-radius:8px}
.back-to-top{margin-top:30px; padding-top:14px; border-top:1px solid var(--line); font-size:14px}

/* 燈箱 */
#lightbox{position:fixed; inset:0; background:rgba(20,20,18,.92); display:none; z-index:100; padding:26px}
#lightbox.open{display:flex; align-items:center; justify-content:center}
#lightbox img{max-width:100%; max-height:100%; object-fit:contain; border-radius:6px; background:#fff}
#lightbox .lb-close{position:absolute; top:14px; right:18px; background:#fff; border:0; border-radius:999px; width:44px; height:44px; font-size:22px; cursor:pointer; line-height:1}
#lightbox .lb-cap{position:absolute; left:0; right:0; bottom:0; background:rgba(0,0,0,.62); color:#fff; padding:11px 18px; font-size:14px; text-align:center}

/* 手機 / 平板 */
@media (max-width:900px){
  .layout{display:block}
  .sidebar{
    position:fixed; inset:0 auto 0 0; width:min(84vw,330px); transform:translateX(-102%);
    transition:transform .22s ease; z-index:60; box-shadow:0 0 40px rgba(0,0,0,.2); height:100vh;
  }
  .sidebar.open{transform:none}
  .main{padding:18px 14px 70px; max-width:none}
  .topbar{margin:-18px -14px 18px; padding:10px 14px}
  #menu-toggle{display:inline-flex; align-items:center; gap:6px; background:var(--brand); color:#fff; border:0; border-radius:8px; padding:8px 13px; font-size:15px; cursor:pointer}
  .sidebar-backdrop{position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:55; display:none}
  .sidebar-backdrop.open{display:block}
  .shot-row{grid-template-columns:1fr}
  .meta>div{grid-template-columns:110px 1fr}
  .chapter{padding:16px 13px}
}
@media (max-width:400px){
  body{font-size:15.4px}
  .topbar .doc-title{font-size:13.5px; width:100%; margin-bottom:4px}
  .search-box{max-width:none}
}

/* 列印 / PDF */
@media print{
  :root{--sidebar:0}
  body{background:#fff; font-size:11.5pt}
  .sidebar,.topbar,.sidebar-backdrop,#lightbox,.back-to-top,.search-box,#menu-toggle{display:none !important}
  .main{padding:0; max-width:none}
  .layout{display:block}
  .chapter{border:0; border-radius:0; padding:0; margin:0 0 18pt; page-break-inside:auto; break-inside:auto}
  .chapter h1{page-break-before:always; break-before:page; padding-top:6pt}
  #ch-cover{page-break-before:auto}
  #ch-cover h1{page-break-before:auto; break-before:auto}
  .chapter h2,.chapter h3{page-break-after:avoid; break-after:avoid}
  .shot,.box,.steps>li,table{page-break-inside:avoid; break-inside:avoid}
  .shot img{max-height:16cm; object-fit:contain}
  .shot-row{display:block}
  a{color:#000; text-decoration:none}
  .table-wrap{overflow:visible}
}
`;

const js = `
(function(){
  var toggle=document.getElementById('menu-toggle');
  var sidebar=document.getElementById('sidebar');
  var backdrop=document.getElementById('sidebar-backdrop');
  function closeNav(){sidebar.classList.remove('open');backdrop.classList.remove('open');toggle.setAttribute('aria-expanded','false');}
  function openNav(){sidebar.classList.add('open');backdrop.classList.add('open');toggle.setAttribute('aria-expanded','true');}
  toggle.addEventListener('click',function(){sidebar.classList.contains('open')?closeNav():openNav();});
  backdrop.addEventListener('click',closeNav);
  sidebar.addEventListener('click',function(e){if(e.target.closest('a')&&window.innerWidth<=900)closeNav();});

  // 目前章節高亮
  var links=[].slice.call(document.querySelectorAll('.toc-link'));
  var targets=links.map(function(l){return document.getElementById(l.dataset.target);}).filter(Boolean);
  var byId={};links.forEach(function(l){byId[l.dataset.target]=l;});
  function highlight(){
    var best=null,bestTop=-Infinity;
    targets.forEach(function(t){
      var top=t.getBoundingClientRect().top-90;
      if(top<=0&&top>bestTop){bestTop=top;best=t;}
    });
    if(!best)best=targets[0];
    links.forEach(function(l){l.classList.remove('active');l.removeAttribute('aria-current');});
    if(best&&byId[best.id]){
      var link=byId[best.id];link.classList.add('active');link.setAttribute('aria-current','true');
      var chapter=best.closest('.chapter');
      if(chapter&&byId[chapter.id]&&byId[chapter.id]!==link)byId[chapter.id].classList.add('active');
    }
  }
  var ticking=false;
  window.addEventListener('scroll',function(){
    if(ticking)return;ticking=true;
    window.requestAnimationFrame(function(){highlight();ticking=false;});
  },{passive:true});
  highlight();

  // 搜尋
  var index=window.__MANUAL_INDEX__||[];
  var input=document.getElementById('search-input');
  var results=document.getElementById('search-results');
  function render(list,q){
    if(!q){results.classList.remove('open');results.innerHTML='';return;}
    if(!list.length){results.innerHTML='<p class="sr-empty">找不到「'+esc(q)+'」相關章節</p>';results.classList.add('open');return;}
    results.innerHTML=list.slice(0,24).map(function(r){
      return '<a href="#'+r.id+'"><span class="sr-ch">'+esc(r.chapter)+'</span>'+esc(r.title)+'</a>';
    }).join('');
    results.classList.add('open');
  }
  function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  input.addEventListener('input',function(){
    var q=input.value.trim().toLowerCase();
    if(!q){render([],'');return;}
    var hits=index.filter(function(r){return r.hay.indexOf(q)>=0;});
    render(hits,input.value.trim());
  });
  input.addEventListener('keydown',function(e){
    if(e.key==='Escape'){input.value='';render([],'');input.blur();}
    if(e.key==='Enter'){var a=results.querySelector('a');if(a){a.click();render([],'');}}
  });
  results.addEventListener('click',function(e){if(e.target.closest('a')){input.value='';render([],'');}});
  document.addEventListener('click',function(e){
    if(!e.target.closest('.search-box'))render([],'');
  });

  // 圖片燈箱
  var lb=document.getElementById('lightbox');
  var lbImg=lb.querySelector('img');
  var lbCap=lb.querySelector('.lb-cap');
  var lastFocus=null,lastScroll=0;
  function openLb(img){
    lastFocus=document.activeElement;lastScroll=window.scrollY;
    lbImg.src=img.src;lbImg.alt=img.alt;
    var cap=img.closest('figure')?img.closest('figure').querySelector('figcaption'):null;
    lbCap.textContent=cap?cap.textContent:img.alt;
    lb.classList.add('open');lb.setAttribute('aria-hidden','false');
    lb.querySelector('.lb-close').focus();
    document.body.style.overflow='hidden';
  }
  function closeLb(){
    lb.classList.remove('open');lb.setAttribute('aria-hidden','true');lbImg.removeAttribute('src');
    document.body.style.overflow='';
    if(lastFocus&&lastFocus.focus)lastFocus.focus();
    window.scrollTo({top:lastScroll});
  }
  document.addEventListener('click',function(e){
    var img=e.target.closest('.shot img');
    if(img){openLb(img);return;}
    if(e.target.closest('#lightbox'))closeLb();
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'&&lb.classList.contains('open'))closeLb();
  });
  document.querySelectorAll('.shot img').forEach(function(img){
    img.setAttribute('tabindex','0');
    img.setAttribute('role','button');
    img.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){e.preventDefault();openLb(img);}
    });
  });
})();
`;

const searchIndex = [];
toc.forEach((c) => {
  searchIndex.push({ id: c.id, title: c.title, chapter: "章節", hay: (c.num + " " + c.title).toLowerCase() });
  c.subs.forEach((s) =>
    searchIndex.push({
      id: s.id,
      title: s.text,
      chapter: `${c.num ? c.num + ". " : ""}${c.title}`,
      hay: `${s.text} ${c.title} ${s.body ?? ""}`.toLowerCase(),
    }),
  );
});

const html = `<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>露營二手 POS — 系統操作手冊 v1.2</title>
<meta name="description" content="露營二手 POS 門市營運系統的完整操作手冊，含實機截圖與逐步操作說明。" />
<style>${css}</style>
</head>
<body id="top">
<a class="skip-link" href="#main-content">跳到主要內容</a>
<div class="layout">
  <nav class="sidebar" id="sidebar" aria-label="手冊目錄">
    <h2 id="toc-title">目錄</h2>
    <ul class="toc" aria-labelledby="toc-title">
      ${tocHtml}
    </ul>
  </nav>
  <div class="sidebar-backdrop" id="sidebar-backdrop"></div>
  <main class="main" id="main-content">
    <div class="topbar">
      <button type="button" id="menu-toggle" aria-expanded="false" aria-controls="sidebar">☰ 目錄</button>
      <span class="doc-title">露營二手 POS — 系統操作手冊 v1.2</span>
      <div class="search-box">
        <label class="sr-only" for="search-input">搜尋章節</label>
        <input type="search" id="search-input" placeholder="搜尋章節關鍵字，例如：退貨" aria-label="搜尋章節" autocomplete="off" />
        <div id="search-results" role="listbox" aria-label="搜尋結果"></div>
      </div>
    </div>
    ${bodyHtml}
  </main>
</div>
<div id="lightbox" aria-hidden="true" role="dialog" aria-modal="true" aria-label="放大檢視圖片">
  <button type="button" class="lb-close" aria-label="關閉放大檢視">✕</button>
  <img alt="" />
  <p class="lb-cap"></p>
</div>
<script>window.__MANUAL_INDEX__=${JSON.stringify(searchIndex)};</script>
<script>${js}</script>
</body>
</html>`;

writeFileSync(OUT, html, "utf8");
const size = statSync(OUT).size;
console.log(`✅ 已產生：${OUT}`);
console.log(`   章節 ${CHAPTERS.length} 章、目錄項目 ${searchIndex.length}、圖片 ${Object.keys(IMAGES).length} 張、缺圖 ${missing}`);
console.log(`   檔案大小 ${(size / 1024 / 1024).toFixed(2)} MB`);
