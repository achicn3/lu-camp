// Isolated real Worker/D1 + backend/Postgres + POS/guest browser acceptance.
// The cash sweep uses its public clock parameter; no ten-minute wall-clock wait.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
assert.ok(new URL(process.env.DATABASE_URL).pathname.includes("_e2e"), "An isolated E2E database is required");
const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const API = "http://127.0.0.1:8116", POS = "http://127.0.0.1:3116", ORDER = "http://127.0.0.1:8794";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lukeng-operating-guards";
mkdirSync(SHOTS, {recursive:true});
let token = "";
async function api(method, path, body) {
  const response = await fetch(`${API}/api/v1${path}`, {method, headers:{Connection:"close",Authorization:`Bearer ${token}`,"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)});
  assert.ok(response.ok, `${path}: ${response.status}`); return response.json();
}
async function until(fn) { for(let i=0;i<90;i++){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,500));}throw Error("Timed out waiting for cloud/POS"); }
token=(await api("POST","/auth/login",{username:"dev-manager",password:process.env.SMOKE_PASSWORD??"dev-test-123456"})).access_token;
const original=await api("GET","/settings");
const run=randomUUID().slice(0,6);
const alertId=randomUUID().replaceAll("-", "");
function localD1(sql) {
  execFileSync(`${ROOT}online-order/node_modules/.bin/wrangler`, ["d1", "execute", "DB", "--local", "--config", "/tmp/lukeng-security-live/wrangler.json", "--persist-to", "/tmp/lukeng-security-live/state", "--command", sql], {cwd:`${ROOT}online-order`,stdio:"pipe",env:process.env});
}
const item=await api("POST","/menu-items",{name:`營運驗收-${run}`,unit_price:"150",category:"營運驗收"});
await api("PATCH",`/menu-items/${item.id}`,{daily_limited:true});
await api("POST",`/menu-daily-stock/item/${item.id}/set`,{qty:2,expected_remaining:0});
const browser=await chromium.launch();
const page=await browser.newPage({viewport:{width:1280,height:1000}});
const guest=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${POS}/login`,{waitUntil:"networkidle"});
  await page.fill('input[name="username"]',"dev-manager");await page.fill('input[name="password"]',process.env.SMOKE_PASSWORD??"dev-test-123456");
  await page.getByRole("button",{name:"登入",exact:true}).click();await page.waitForURL(u=>!u.pathname.startsWith("/login"));
  await api("PATCH","/settings",{online_order_hours:{enabled:false,days:Array(7).fill(null)}});
  await page.goto(`${POS}/menu?section=online`,{waitUntil:"networkidle"});
  await page.getByText("營業時間",{exact:true}).click();
  await page.getByLabel("依營業時間接單").check();
  await page.getByRole("button",{name:"儲存營業時間",exact:true}).click();
  await page.getByText(/已儲存，請按/).waitFor();
  await page.getByRole("button",{name:"發佈到線上點餐",exact:true}).click();
  await page.getByText(/已發佈/).waitFor();
  await api("PUT","/online-orders/accepting",{accepting:true});
  await until(async()=>!(await(await fetch(`${ORDER}/api/status`)).json()).within_hours);
  await page.screenshot({path:`${SHOTS}/01-hours-settings.png`});
  await guest.goto(ORDER,{waitUntil:"networkidle"});
  await guest.getByText("目前非營業時間，可先看看菜單。",{exact:true}).waitFor();
  await guest.screenshot({path:`${SHOTS}/02-closed-mobile.png`});
  const blocked=await fetch(`${ORDER}/api/orders`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({idempotency_key:randomUUID(),table_code:null,payment_method:"CASH",turnstile_token:"XXXX.DUMMY.TOKEN.XXXX",lines:[{item_id:item.id,option_ids:[],qty:1}]})});
  assert.equal(blocked.status,503);
  await page.getByLabel("依營業時間接單").uncheck();
  await page.getByRole("button",{name:"儲存營業時間",exact:true}).click();await page.getByText(/已儲存，請按/).waitFor();
  await page.getByRole("button",{name:"發佈到線上點餐",exact:true}).click();
  await until(async()=>(await(await fetch(`${ORDER}/api/status`)).json()).accepting);
  await guest.reload({waitUntil:"networkidle"});
  await guest.getByRole("button",{name:"營運驗收",exact:true}).click();
  await guest.locator(`.item[data-item-id="${item.id}"] .item-detail`).click();
  await guest.locator("#detail-add").click();
  await guest.getByRole("button",{name:/購物車 1 份/}).click();
  await guest.getByText("現金訂單請在送出後 10 分鐘內結帳，逾時自動取消。",{exact:true}).waitFor();
  await guest.getByRole("button",{name:"送出現金訂單",exact:true}).click();
  await guest.waitForURL(/\/order\/[A-Za-z0-9_-]+$/,{timeout:45000});
  const cash=await until(async()=>(await api("GET","/online-orders")).orders.find(o=>o.lines.some(l=>l.item_id===item.id)));
  assert.equal(cash.hold_status,"HELD");
  await guest.screenshot({path:`${SHOTS}/03-cash-order.png`});
  // Advance only the service's sweep clock, exercising real locks/releases/outbox.
  execFileSync("uv",["run","--no-sync","--directory",`${ROOT}backend`,"python","-c",`
import asyncio
from datetime import timedelta
from app.core.db import get_sessionmaker
from app.core.time import utc_now
from app.modules.onlineorder.orders_service import OnlineOrdersService
async def main():
    async with get_sessionmaker()() as session:
        await OnlineOrdersService(session, None).expire_reservations(1, now=utc_now()+timedelta(minutes=10,seconds=1))
        await session.commit()
asyncio.run(main())
`],{stdio:"pipe",env:process.env});
  await until(async()=>(await api("GET","/online-orders")).orders.find(o=>o.id===cash.id)?.payment_status==="CANCELLED");
  await until(async()=>(await(await fetch(`${ORDER}/api/menu`)).json()).items.find(i=>i.id===item.id)?.remaining===2);
  await guest.getByText("這張訂單已取消。",{exact:true}).waitFor({timeout:45000});
  await guest.screenshot({path:`${SHOTS}/04-cancelled-mobile.png`});
  await page.goto(`${POS}/pos`,{waitUntil:"networkidle"});await page.getByRole("button",{name:/線上訂單/}).click();
  const row=page.locator(".online-order").filter({hasText:item.name});
  assert.match(await row.innerText(),/已取消/);
  assert.equal(await row.getByRole("button",{name:"帶入結帳"}).count(),0);
  await page.screenshot({path:`${SHOTS}/05-cancelled-pos.png`});
  // Simulated provider state in isolated local D1; no real payment is requested.
  localD1(`INSERT INTO orders (id,store_id,token_hash,idem_key,fingerprint,device_id,ip_hash,table_label,service_mode,menu_version,total,payment_method,payment_status,sync_status,hold_status,created_at,updated_at,linepay_started_at,linepay_transaction_id) VALUES ('${alertId}',1,'${alertId}','${alertId}','fixture','fixture','fixture','付款驗收','TAKEOUT',1,150,'LINE_PAY','PENDING','IMPORTED','NONE',${Date.now()},${Date.now()},${Date.now()-660000},'202610140001')`);
  await until(async()=>(await api("GET","/online-orders")).payment_alerts.some(a=>a.id===alertId));
  await page.getByRole("heading",{name:"付款待確認 1 筆",exact:true}).waitFor({timeout:30000});
  await page.getByText("202610140001",{exact:false}).waitFor();
  await page.screenshot({path:`${SHOTS}/06-payment-alert.png`});
  localD1(`UPDATE orders SET payment_status='CANCELLED',sync_status='VOIDED' WHERE id='${alertId}'`);
  await until(async()=>!(await api("GET","/online-orders")).payment_alerts.some(a=>a.id===alertId));
  await page.getByRole("heading",{name:"付款待確認 1 筆",exact:true}).waitFor({state:"hidden",timeout:30000});
  await page.setViewportSize({width:390,height:844});
  await page.goto(`${POS}/menu?section=online`,{waitUntil:"networkidle"});
  await page.getByText("營業時間",{exact:true}).click();
  await page.getByLabel("依營業時間接單").check();
  await page.getByLabel("星期一 營業",{exact:true}).check();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), "mobile page must not overflow");
  await page.screenshot({path:`${SHOTS}/07-hours-mobile.png`,fullPage:true});
  console.log("PASS payment alert Worker → Postgres → POS and clear, mobile settings without overflow");
  console.log("PASS operating hours saved/published via POS, closed request rejected, real guest cash order auto-cancelled, stock restored, POS and guest cancelled");
} finally {
  localD1(`DELETE FROM orders WHERE id='${alertId}'`);
  await browser.close();
  await api("PATCH","/settings",{online_order_hours:original.online_order_hours});
  await api("POST","/online-order/publish");
}
