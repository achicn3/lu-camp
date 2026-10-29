// 寄售帳篷上官網（docs/97 §5）：店內 POS 推清單與照片，官網公開讀。
// 版本只往前；照片要先傳才收清單；沒被清單引用的帳篷照片（售出、下架）一律刪掉；
// 公開讀取只給官網網域 CORS。
import { exports } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { get, integration, sha256Hex, webp } from "./helpers";

type Tent = Record<string, unknown>;

function tent(overrides: Tent = {}): Tent {
  return {
    id: "T-2611",
    brand: "Snow Peak",
    name: "Amenity Dome M",
    color: "象牙白",
    nights: 6,
    missing: [],
    defects: ["外帳頂部一處淡色污漬"],
    extras: ["原廠地墊"],
    photos: [],
    video: "https://youtu.be/cSArnUe7cEY",
    price: 6800,
    status: "available",
    listedAt: "2026-10-28",
    updatedAt: "2026-10-30T11:05:00+08:00",
    ...overrides,
  };
}

function feed(version: number, tents: Tent[]): string {
  return JSON.stringify({ version, syncedAt: "2026-10-30T14:20:00+08:00", soldDays: 30, tents });
}

async function putPhoto(seed: number): Promise<string> {
  const data = webp(seed);
  const hash = await sha256Hex(data);
  const resp = await integration("PUT", `/integration/tent-photos/${hash}`, data);
  expect(resp.status).toBe(201);
  return hash;
}

describe("寄售帳篷清單", () => {
  it("還沒推過：回空清單", async () => {
    const resp = await get("/api/tents");
    expect(resp.status).toBe(200);
    expect(await resp.json()).toEqual({ meta: { syncedAt: null, soldDays: 30 }, tents: [] });
  });

  it("推上去後公開讀取，照片轉成絕對網址", async () => {
    const hash = await putPhoto(1);
    const put = await integration("PUT", "/integration/tents", feed(1, [tent({ photos: [hash] })]));
    expect(put.status).toBe(200);
    const resp = await get("/api/tents");
    expect(resp.headers.get("Cache-Control")).toBe("public, max-age=60");
    const body = (await resp.json()) as { meta: Tent; tents: Tent[] };
    expect(body.meta).toEqual({ syncedAt: "2026-10-30T14:20:00+08:00", soldDays: 30 });
    expect(body.tents[0]?.photos).toEqual([`https://order.test/tent-photos/${hash}.webp`]);
    expect(body.tents[0]?.name).toBe("Amenity Dome M");
    const photo = await get(`/tent-photos/${hash}.webp`);
    expect(photo.status).toBe(200);
    expect(photo.headers.get("Content-Type")).toBe("image/webp");
  });

  it("沒簽章：401", async () => {
    const resp = await exportsFetch("PUT", "/integration/tents", feed(1, []));
    expect(resp.status).toBe(401);
  });

  it("照片還沒傳：409 並列出缺哪些，清單不更新", async () => {
    const missing = await sha256Hex(webp(9));
    const resp = await integration("PUT", "/integration/tents", feed(1, [tent({ photos: [missing] })]));
    expect(resp.status).toBe(409);
    expect(await resp.json()).toEqual({ error: "missing_photo", missing: [missing] });
    expect(((await (await get("/api/tents")).json()) as { tents: Tent[] }).tents).toEqual([]);
  });

  it("版本只往前：舊版 409、同版同內容冪等、同版不同內容 409", async () => {
    expect((await integration("PUT", "/integration/tents", feed(5, [tent()]))).status).toBe(200);
    expect((await integration("PUT", "/integration/tents", feed(5, [tent()]))).status).toBe(200);
    const other = await integration("PUT", "/integration/tents", feed(5, [tent({ price: 1 })]));
    expect(other.status).toBe(409);
    const older = await integration("PUT", "/integration/tents", feed(4, []));
    expect(older.status).toBe(409);
    expect(await older.json()).toEqual({ error: "version_conflict" });
  });

  it("售出、下架後沒被引用的照片從雲端刪掉；菜單照片不受影響", async () => {
    const keep = await putPhoto(1);
    const drop = await putPhoto(2);
    const menuData = webp(3);
    const menuHash = await sha256Hex(menuData);
    expect((await integration("PUT", `/integration/photos/${menuHash}`, menuData)).status).toBe(201);
    await integration("PUT", "/integration/tents", feed(1, [tent({ photos: [keep, drop] })]));
    const sold = tent({ id: "T-1", status: "sold", photos: [] });
    const resp = await integration("PUT", "/integration/tents", feed(2, [tent({ photos: [keep] }), sold]));
    expect(resp.status).toBe(200);
    expect((await get(`/tent-photos/${keep}.webp`)).status).toBe(200);
    expect((await get(`/tent-photos/${drop}.webp`)).status).toBe(404);
    expect((await get(`/photos/${menuHash}.webp`)).status).toBe(200);
  });

  it("退貨回庫照片又出現：補傳後清單收下", async () => {
    const hash = await putPhoto(1);
    await integration("PUT", "/integration/tents", feed(1, [tent({ photos: [hash] })]));
    await integration("PUT", "/integration/tents", feed(2, [tent({ status: "sold" })]));
    expect((await get(`/tent-photos/${hash}.webp`)).status).toBe(404);
    const again = await integration("PUT", "/integration/tents", feed(3, [tent({ photos: [hash] })]));
    expect(again.status).toBe(409);
    await putPhoto(1);
    expect((await integration("PUT", "/integration/tents", feed(3, [tent({ photos: [hash] })]))).status).toBe(200);
  });

  it.each([
    ["status 不在清單裡", { status: "deleted" }],
    ["價格有小數", { price: 10.5 }],
    ["照片不是雜湊", { photos: ["../../etc"] }],
    ["缺件不是字串陣列", { missing: [1] }],
    ["名稱太長", { name: "x".repeat(201) }],
    ["影片不是 YouTube", { video: "javascript:alert(1)" }],
  ])("格式不對 422：%s", async (_label, overrides) => {
    const resp = await integration("PUT", "/integration/tents", feed(1, [tent(overrides)]));
    expect(resp.status).toBe(422);
  });

  it("CORS 只給官網網域", async () => {
    const ok = await get("/api/tents", { Origin: "https://lukeng-camp.com" });
    expect(ok.headers.get("Access-Control-Allow-Origin")).toBe("https://lukeng-camp.com");
    expect(ok.headers.get("Vary")).toContain("Origin");
    const www = await get("/api/tents", { Origin: "https://www.lukeng-camp.com" });
    expect(www.headers.get("Access-Control-Allow-Origin")).toBe("https://www.lukeng-camp.com");
    const evil = await get("/api/tents", { Origin: "https://evil.example" });
    expect(evil.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });

  it("帳篷照片只收 WebP 且雜湊要對", async () => {
    const hash = await sha256Hex(webp(1));
    expect((await integration("PUT", `/integration/tent-photos/${hash}`, webp(2))).status).toBe(422);
    expect((await get(`/tent-photos/${"0".repeat(64)}.webp`)).status).toBe(404);
  });
});

function exportsFetch(method: string, path: string, body: string): Promise<Response> {
  return exports.default.fetch(new Request(`https://order.test${path}`, { method, body }));
}
