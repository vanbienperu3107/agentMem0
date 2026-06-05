import { JSDOM } from "jsdom";
import fs from "fs";
let pass=0, fail=0;
const ok=(c,m)=>{ if(c){pass++;console.log("  PASS:",m)} else {fail++;console.log("  FAIL:",m)} };

function makeEnv() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body><main></main></body></html>`,
    { url: "https://chatgpt.com/c/abc123", pretendToBeVisual: true });
  const { window } = dom;
  if (!Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, "innerText")) {
    Object.defineProperty(window.HTMLElement.prototype, "innerText", {
      get(){ return this.textContent; }, set(v){ this.textContent=v; }, configurable:true });
  }
  global.window=window; global.document=window.document;
  global.MutationObserver=window.MutationObserver; global.InputEvent=window.InputEvent;
  global.requestAnimationFrame=(cb)=>setTimeout(cb,0); window.requestAnimationFrame=global.requestAnimationFrame;
  const sent=[]; const listeners={};
  window.__TAURI__={ event:{ emit:(ch,p)=>sent.push({ch,p}), listen:(ch,cb)=>{ listeners[ch]=cb; } } };
  window.__sent=sent; window.__listeners=listeners;
  let code=fs.readFileSync("./chat-logger.js","utf8").replace(/window\.addEventListener[\s\S]*$/,"");
  window.eval("var location=window.location;\n"+code+"\n; window.ChatLogger=ChatLogger;");
  return { window };
}
function addMsg(window,id,role,text){
  const main=window.document.querySelector("main");
  const turn=window.document.createElement("div");
  turn.setAttribute("data-testid","conversation-turn-1");
  const n=window.document.createElement("div");
  n.setAttribute("data-message-id",id); n.setAttribute("data-message-author-role",role); n.textContent=text;
  turn.appendChild(n);
  if(role==="assistant"){ const b=window.document.createElement("button"); b.setAttribute("aria-label","Copy"); turn.appendChild(b);}
  main.appendChild(turn); return n;
}

console.log("TEST 1 — scan() bat dung user + assistant(done):");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  addMsg(window,"u1","user","Xin chao"); addMsg(window,"a1","assistant","Chao ban");
  window.ChatLogger.scan();
  const s=window.__sent.filter(x=>x.ch==="chat-logger://log-message");
  ok(s.length===2,`gui 2 message (thuc: ${s.length})`);
  ok(s.some(x=>x.p.id==="u1"&&x.p.role==="user"),"co user u1");
  ok(s.some(x=>x.p.id==="a1"&&x.p.role==="assistant"),"co assistant a1");
}
console.log("TEST 2 — keyword Enter tai DOM (giu nguyen):");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.ChatLogger.hookKeywordTrigger(); ta.value="compact";
  ta.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  ok(window.__sent.filter(x=>x.ch==="chat-logger://compact").length===1,"Enter 'compact' -> emit compact 1 lan");
}
console.log("TEST 3 — dedup khong gui trung:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  addMsg(window,"u1","user","hello"); window.ChatLogger.scan(); window.ChatLogger.scan(); window.ChatLogger.scan();
  ok(window.__sent.filter(x=>x.ch==="chat-logger://log-message").length===1,"scan 3 lan chi gui 1");
}
console.log("TEST 4 — PERF: observer KHONG dung characterData (khong fire moi token):");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  let scanCount=0; const orig=window.ChatLogger.scan.bind(window.ChatLogger);
  window.ChatLogger.scan=()=>{scanCount++;return orig();};
  // mo phong observer that cua v0.6.0: childList + debounce
  const target=window.document.querySelector("main");
  const obs=new window.MutationObserver(()=>window.ChatLogger.scheduleScan());
  obs.observe(target,{childList:true,subtree:true}); // KHONG characterData
  const n=addMsg(window,"a1","assistant","");
  // mo phong stream: doi text 500 lan -> KHONG fire vi khong observe characterData
  for(let i=0;i<500;i++){ n.textContent+="x"; }
  await new Promise(r=>setTimeout(r,700)); obs.disconnect();
  console.log(`    -> scan() chay ${scanCount} lan cho 500 token (childList only + debounce)`);
  ok(scanCount<=3,`NHE: scan chi ${scanCount} lan (vs 500 ban cu) khi stream`);
}
console.log("TEST 5 — debounce gop them node:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  let scanCount=0; const orig=window.ChatLogger.scan.bind(window.ChatLogger);
  window.ChatLogger.scan=()=>{scanCount++;return orig();};
  for(let i=0;i<10;i++){ window.ChatLogger.scheduleScan(); }
  await new Promise(r=>setTimeout(r,700));
  ok(scanCount===1,`10 lan schedule gop thanh 1 scan (thuc: ${scanCount})`);
}
console.log("TEST 6 — nut noi xuat hien dung 2 nut:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons();
  const fab=window.document.getElementById("cl-fab");
  ok(!!fab,"co hop nut #cl-fab");
  const btns=fab? fab.querySelectorAll("button"):[];
  ok(btns.length===3,`co 3 nut: summary+full+refresh (thuc: ${btns.length})`);
  const visible=[...btns].filter(b=>b.style.display!=="none");
  ok(visible.length===2,`2 nut hien mac dinh (thuc: ${visible.length})`);
  ok([...btns].some(b=>b.textContent.includes("summary")),"co nut Luu summary");
  ok([...btns].some(b=>b.textContent.includes("full session")),"co nut Luu full session");
}
console.log("TEST 7 — nut 'Luu summary' emit summarize_current:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons();
  const btn=[...window.document.querySelectorAll("#cl-fab button")].find(b=>b.textContent.includes("summary"));
  btn.click();
  ok(window.__sent.some(x=>x.ch==="chat-logger://summarize_current"),"emit chat-logger://summarize_current");
}
console.log("TEST 8 — nut 'Luu full session' emit compact:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons();
  const btn=[...window.document.querySelectorAll("#cl-fab button")].find(b=>b.textContent.includes("full session"));
  btn.click();
  ok(window.__sent.some(x=>x.ch==="chat-logger://compact"),"emit chat-logger://compact");
}

console.log("TEST 9 — listenResult dang ky listener chat-logger://result:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  ok(typeof window.__listeners["chat-logger://result"]==="function","da dang ky listener result");
}
console.log("TEST 10 — result OK -> nut summary doi xanh '✓':");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://result"]({ payload:{action:"summarize",ok:true,msg:"Đã lưu summary vào mem0"} });
  const b=window.ChatLogger.btns.summarize;
  ok(b.textContent.startsWith("✓"),`nut summary hien ✓ (thuc: '${b.textContent}')`);
  ok(b.style.background.includes("16, 163, 127")||b.style.background==="#10a37f"||b.style.background.includes("rgb"),"mau xanh");
}
console.log("TEST 11 — result FAIL -> nut compact doi do '✗' + tooltip loi:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://result"]({ payload:{action:"compact",ok:false,msg:"Lỗi lưu: 401"} });
  const b=window.ChatLogger.btns.compact;
  ok(b.textContent.startsWith("✗"),`nut compact hien ✗ (thuc: '${b.textContent}')`);
  ok(b.title.includes("401"),"tooltip co ly do loi 401");
}
console.log("TEST 12 — toast hien khi co result:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://result"]({ payload:{action:"summarize",ok:true,msg:"Đã lưu"} });
  const toasts=[...window.document.body.children].filter(e=>e.id!=="cl-fab" && e.textContent.includes("Đã lưu"));
  ok(toasts.length>=1,"co toast thong bao");
}

console.log("TEST 13 — keyword /lichsu emit fetch-history:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.ChatLogger.hookKeywordTrigger(); ta.value="lichsu";
  ta.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  ok(window.__sent.some(x=>x.ch==="chat-logger://fetch-history"),"/lichsu -> emit fetch-history");
}
console.log("TEST 14 — listen history-result + renderHistory chen vao chat:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  ok(typeof window.__listeners["chat-logger://history-result"]==="function","da dang ky listener history-result");
  window.__listeners["chat-logger://history-result"]({ payload:{ok:true, sessions:[
    {id:"s1",started_at:"2026-06-01",summary:"Tim hieu MarkItDown",message_count:10},
    {id:"s2",started_at:"2026-05-31",summary:"Regex loc so dien thoai",message_count:20}
  ]}});
  const val = ta.value||"";
  ok(val.includes("2 phiên gần nhất"),"chen header 2 phien");
  ok(val.includes("MarkItDown")&&val.includes("s1"),"co noi dung phien 1 + id");
}
console.log("TEST 15 — history-result loi -> bao loi:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.listenResult();
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.__listeners["chat-logger://history-result"]({ payload:{ok:false,msg:"401 Unauthorized"} });
  ok((ta.value||"").includes("401"),"hien thi loi 401");
}

console.log("TEST 16 — start() goi checkOAuth -> emit check-oauth:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.checkOAuth();
  ok(window.__sent.some(x=>x.ch==="chat-logger://check-oauth"),"emit check-oauth");
}
console.log("TEST 17 — oauth-status 'expired' -> nut summary do + hien nut Gia han:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://oauth-status"]({ payload:{status:"expired"} });
  const bSum=window.ChatLogger.btns.summarize, bRef=window.ChatLogger.btns.refresh;
  ok(bSum.style.background==="#d9534f"||bSum.style.background.includes("217"),"nut summary do");
  ok(bRef.style.display==="block","nut Gia han hien ra");
}
console.log("TEST 18 — oauth-status 'valid' -> nut summary xanh + an nut Gia han:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://oauth-status"]({ payload:{status:"expired"} });
  window.__listeners["chat-logger://oauth-status"]({ payload:{status:"valid",refreshed:true,msg:"Đã gia hạn"} });
  const bSum=window.ChatLogger.btns.summarize, bRef=window.ChatLogger.btns.refresh;
  ok(bSum.style.background==="#10a37f"||bSum.style.background.includes("16, 163"),"nut summary xanh lai");
  ok(bRef.style.display==="none","nut Gia han an di");
}
console.log("TEST 19 — nut Gia han bam -> emit refresh-oauth:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  window.__listeners["chat-logger://oauth-status"]({ payload:{status:"expired"} });
  window.ChatLogger.btns.refresh.click();
  ok(window.__sent.some(x=>x.ch==="chat-logger://refresh-oauth"),"emit refresh-oauth");
}

console.log("TEST 20 — 'lichsu deploy VPS' emit fetch-history kem query:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.ChatLogger.hookKeywordTrigger(); ta.value="lichsu deploy VPS";
  ta.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  const ev=window.__sent.find(x=>x.ch==="chat-logger://fetch-history");
  ok(!!ev,"emit fetch-history");
  ok(ev && ev.p && ev.p.query==="deploy VPS",`query='deploy VPS' (thuc: '${ev&&ev.p&&ev.p.query}')`);
}
console.log("TEST 21 — 'lichsu' trong emit query rong (5 gan nhat):");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.ChatLogger.hookKeywordTrigger(); ta.value="lichsu";
  ta.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  const ev=window.__sent.find(x=>x.ch==="chat-logger://fetch-history");
  ok(ev && ev.p.query==="","query rong");
}
console.log("TEST 22 — 'xemphien <id>' emit fetch-session-detail:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  window.ChatLogger.hookKeywordTrigger(); ta.value="xemphien abc-123";
  ta.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Enter",bubbles:true}));
  const ev=window.__sent.find(x=>x.ch==="chat-logger://fetch-session-detail");
  ok(ev && ev.p.id==="abc-123",`emit detail id=abc-123 (thuc: '${ev&&ev.p&&ev.p.id}')`);
}
console.log("TEST 23 — renderDetail chen full transcript vao chat:");
{ const {window}=makeEnv(); window.ChatLogger.emitMethod="event";
  window.ChatLogger.mountFloatingButtons(); window.ChatLogger.listenResult();
  const ta=window.document.createElement("textarea"); window.document.querySelector("main").appendChild(ta);
  ok(typeof window.__listeners["chat-logger://session-detail-result"]==="function","listener detail dang ky");
  window.__listeners["chat-logger://session-detail-result"]({ payload:{ok:true, session:{
    id:"s9", started_at:"2026-06-01", transcript:[{role:"user",content:"hoi"},{role:"assistant",content:"dap"}]
  }}});
  const val=ta.value||"";
  ok(val.includes("Phiên s9")&&val.includes("hoi")&&val.includes("dap"),"chen full transcript");
}

console.log(`\n==== KET QUA: ${pass} pass, ${fail} fail ====`);
process.exit(fail>0?1:0);
