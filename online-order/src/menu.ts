// 菜單快照（docs/44 §3.5）：店內 POS 發佈、客人讀目前生效的版本。
// 快照內容由店內 backend 組好（已驗證過的菜單），這裡只檢查形狀，擋住格式錯誤的發佈。
import { error, json, sha256Hex } from "./http";
import {
  BREW_ARTS, BREW_EFFECTS, BREW_THEMES, RETAIL_ROLES, UPSELL_ROLES, type MenuSnapshot,
} from "./client/types";
import type { AvailabilityUpdate } from "./availability";

export const MENU_MAX_BYTES = 512 * 1024;
const KEEP_VERSIONS = 5;

function isPositiveInt(v: unknown): v is number {
  return typeof v === "number" && Number.isSafeInteger(v) && v > 0;
}

function isWholeYuan(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v) && v >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function keysAre(value: Record<string, unknown>, required: string[], optional: string[] = []): boolean {
  return required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
}

function validDate(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return value >= "0001-01-01" && !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function validPresentation(value: unknown): boolean {
  if (!isRecord(value) || !keysAre(value, [
    "flavor_description", "audience_description", "is_recommended", "is_new",
    "limited_on", "show_remaining", "low_stock_threshold", "hide_sold_out",
  ], ["role"])) return false;
  if (Object.hasOwn(value, "role") && value.role !== null &&
    !(UPSELL_ROLES as readonly unknown[]).includes(value.role)) return false;
  const shortText = (text: unknown) => text === null ||
    (typeof text === "string" && [...text].length <= 120);
  return shortText(value.flavor_description) && shortText(value.audience_description) &&
    [value.is_recommended, value.is_new, value.show_remaining, value.hide_sold_out]
      .every((flag) => typeof flag === "boolean") &&
    validDate(value.limited_on) && Number.isInteger(value.low_stock_threshold) &&
    (value.low_stock_threshold as number) >= 0 && (value.low_stock_threshold as number) <= 9999;
}

function validGroups(value: unknown): boolean {
  return Array.isArray(value) && value.every((group: unknown) =>
    isRecord(group) && keysAre(group, ["id", "name", "min_select", "max_select", "options"]) &&
    Array.isArray(group.options) && group.options.every((option: unknown) =>
      isRecord(option) && keysAre(option, ["id", "name", "price_delta", "available", "remaining"])),
  );
}

function textUpTo(value: unknown, max: number, nullable = true): boolean {
  if (value === null) return nullable;
  return typeof value === "string" && [...value].length <= max && (nullable || value.length > 0);
}

/** 手沖體驗卡（docs/63 §4）：只收公開欄位；引用的品項必須在同一份快照裡。 */
function validExperiences(value: unknown, itemIds: Set<unknown>): boolean {
  if (value === undefined) return true;
  return Array.isArray(value) && value.every((e: unknown) =>
    isRecord(e) && keysAre(e, ["id", "item_id", "option_ids", "title", "tag", "origin", "notes",
      "description", "includes", "theme", "art", "effect"]) &&
    isPositiveInt(e.id) && itemIds.has(e.item_id) &&
    Array.isArray(e.option_ids) && e.option_ids.length <= 10 && e.option_ids.every(isPositiveInt) &&
    textUpTo(e.title, 30, false) && textUpTo(e.tag, 12) && textUpTo(e.origin, 60) &&
    textUpTo(e.notes, 80) && textUpTo(e.description, 300) &&
    Array.isArray(e.includes) && e.includes.length <= 5 && e.includes.every((inc: unknown) =>
      isRecord(inc) && keysAre(inc, ["title", "detail"]) && textUpTo(inc.title, 20, false) &&
      textUpTo(inc.detail, 60)) &&
    (BREW_THEMES as readonly unknown[]).includes(e.theme) &&
    (BREW_ARTS as readonly unknown[]).includes(e.art) &&
    (BREW_EFFECTS as readonly unknown[]).includes(e.effect));
}

/** 帶回家商品（docs/63 §13）：只收公開欄位，不收成本／SKU；角色只能是咖啡豆或濾掛。 */
function validRetail(value: unknown): boolean {
  if (value === undefined) return true;
  if (!Array.isArray(value)) return false;
  const seen = new Set<unknown>();
  return value.every((r: unknown) => {
    if (!isRecord(r) || !keysAre(r, ["id", "name", "description", "category", "unit_price", "photo",
      "role", "available", "remaining"])) return false;
    if (!isPositiveInt(r.id) || seen.has(r.id)) return false;
    seen.add(r.id);
    return textUpTo(r.name, 150, false) && textUpTo(r.description, 300) && textUpTo(r.category, 100) &&
      isWholeYuan(r.unit_price) && (r.photo === null || (typeof r.photo === "string" && /^[0-9a-f]{64}$/.test(r.photo))) &&
      (r.role === null || (RETAIL_ROLES as readonly unknown[]).includes(r.role)) &&
      typeof r.available === "boolean" && Number.isSafeInteger(r.remaining) && (r.remaining as number) >= 0;
  });
}

/** 「不知道喝什麼」引導推薦（docs/63 §2 M2a）：1–3 題、每題 2–4 個答案；引用的品項／體驗卡要在同一份快照裡。 */
function validQuiz(value: unknown, itemIds: Set<unknown>, experienceIds: Set<unknown>): boolean {
  if (value === undefined) return true;
  if (!isRecord(value) || !keysAre(value, ["questions"]) || !Array.isArray(value.questions)) return false;
  const { questions } = value;
  return questions.length >= 1 && questions.length <= 3 && questions.every((q: unknown) =>
    isRecord(q) && keysAre(q, ["prompt", "options"]) && textUpTo(q.prompt, 30, false) &&
    Array.isArray(q.options) && q.options.length >= 2 && q.options.length <= 4 &&
    q.options.every((o: unknown) =>
      isRecord(o) && keysAre(o, ["label", "items"]) && textUpTo(o.label, 20, false) &&
      Array.isArray(o.items) && o.items.length <= 30 && o.items.every((r: unknown) =>
        isRecord(r) && keysAre(r, ["kind", "id"]) &&
        ((r.kind === "item" && itemIds.has(r.id)) || (r.kind === "experience" && experienceIds.has(r.id))))));
}

export function validSnapshot(s: unknown): s is { version: number; published_at: string } {
  if (typeof s !== "object" || s === null) return false;
  const o = s as Record<string, unknown>;
  if (!isPositiveInt(o.version) || typeof o.published_at !== "string") return false;
  if (!Array.isArray(o.categories) || !Array.isArray(o.items)) return false;
  const itemIds = new Set(o.items.map((item: unknown) => (isRecord(item) ? item.id : undefined)));
  if (!validExperiences(o.experiences, itemIds)) return false;
  if (!validRetail(o.retail)) return false;
  const experienceIds = new Set(
    Array.isArray(o.experiences) ? o.experiences.map((e: unknown) => (isRecord(e) ? e.id : undefined)) : [],
  );
  if (!validQuiz(o.quiz, itemIds, experienceIds)) return false;
  return o.items.every((item: unknown) => {
    if (!isRecord(item)) return false;
    const i = item as Record<string, unknown>;
    return keysAre(i, ["id", "name", "description", "category_id", "unit_price", "photo",
      "available", "remaining", "option_groups"], ["presentation"]) &&
      isPositiveInt(i.id) && typeof i.name === "string" && isWholeYuan(i.unit_price) &&
      validGroups(i.option_groups) &&
      (!Object.hasOwn(i, "presentation") || validPresentation(i.presentation));
  });
}

export async function publishMenu(env: Env, storeId: number, raw: Uint8Array): Promise<Response> {
  let snapshot: unknown;
  try {
    snapshot = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return error("invalid_json", 422);
  }
  if (!validSnapshot(snapshot)) return error("invalid_snapshot", 422);
  const text = JSON.stringify(snapshot);
  const digest = await sha256Hex(new TextEncoder().encode(text));
  // 先比目前生效的版本：比它舊的一律拒收（即使內容和當年一樣——發佈只能往前）。
  const meta = await env.DB.prepare("SELECT menu_version FROM stores_meta WHERE store_id = ?")
    .bind(storeId)
    .first<{ menu_version: number }>();
  if (meta !== null && snapshot.version < meta.menu_version) return error("version_conflict", 409);
  const existing = await env.DB.prepare(
    "SELECT sha256 FROM menu_snapshots WHERE store_id = ? AND version = ?",
  )
    .bind(storeId, snapshot.version)
    .first<{ sha256: string }>();
  if (existing !== null) {
    return existing.sha256 === digest ? json({ version: snapshot.version }) : error("version_conflict", 409);
  }
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO menu_snapshots (store_id, version, json, sha256, published_at) VALUES (?, ?, ?, ?, ?)",
    ).bind(storeId, snapshot.version, text, digest, snapshot.published_at),
    env.DB.prepare(
      "INSERT INTO stores_meta (store_id, menu_version) VALUES (?, ?) " +
        "ON CONFLICT (store_id) DO UPDATE SET menu_version = excluded.menu_version " +
        "WHERE excluded.menu_version > stores_meta.menu_version",
    ).bind(storeId, snapshot.version),
    env.DB.prepare(
      "DELETE FROM menu_snapshots WHERE store_id = ? AND version NOT IN " +
        "(SELECT version FROM menu_snapshots WHERE store_id = ? ORDER BY version DESC LIMIT ?)",
    ).bind(storeId, storeId, KEEP_VERSIONS),
  ]);
  return json({ version: snapshot.version });
}

async function loadEffectiveMenu(
  env: Env, storeId: number,
): Promise<{ menu: MenuSnapshot; revision: number } | null> {
  const row = await env.DB.prepare(
    "SELECT s.json, a.json AS availability, a.revision FROM stores_meta m JOIN menu_snapshots s " +
      "ON s.store_id = m.store_id AND s.version = m.menu_version " +
      "LEFT JOIN menu_availability a ON a.store_id = m.store_id AND a.menu_version = m.menu_version " +
      "WHERE m.store_id = ?",
  )
    .bind(storeId)
    .first<{ json: string; availability: string | null; revision: number | null }>();
  if (row === null) return null;
  const menu = JSON.parse(row.json) as MenuSnapshot;
  if (row.availability === null) return { menu, revision: 0 };
  const state = JSON.parse(row.availability) as AvailabilityUpdate;
  const items = new Map(state.items.map((entry) => [entry.id, entry]));
  const options = new Map(state.options.map((entry) => [entry.id, entry]));
  const retail = new Map((state.retail ?? []).map((entry) => [entry.id, entry]));
  const popular = new Map((state.popular ?? []).map((entry) => [entry.id, entry.rank]));
  return {
    revision: row.revision ?? 0,
    menu: {
      ...menu,
      items: menu.items.map((item) => {
        const current = items.get(item.id);
        const rank = popular.get(item.id);
        return {
          ...item,
          available: current?.available ?? false,
          remaining: current?.remaining ?? null,
          ...(rank === undefined ? {} : { popularity: rank }),
          option_groups: item.option_groups.map((group) => ({
            ...group,
            options: group.options.map((option) => {
              const updated = options.get(option.id);
              return {
                ...option,
                available: updated?.available ?? false,
                remaining: updated?.remaining ?? null,
              };
            }),
          })),
        };
      }),
      // 帶回家商品：同步沒列到＝不可賣（下架、或可售同步較舊的店內版本）。
      ...(menu.retail === undefined ? {} : {
        retail: menu.retail.map((product) => {
          const current = retail.get(product.id);
          return { ...product, available: current?.available ?? false, remaining: current?.remaining ?? 0 };
        }),
      }),
    },
  };
}

/** 客人看到與送單驗價用同一份目前生效菜單。 */
export async function currentEffectiveMenu(env: Env, storeId: number): Promise<MenuSnapshot | null> {
  return (await loadEffectiveMenu(env, storeId))?.menu ?? null;
}

export async function readMenu(env: Env, storeId: number, req: Request): Promise<Response> {
  const current = await loadEffectiveMenu(env, storeId);
  if (current === null) return error("menu_not_published", 404);
  const etag = `"menu-v${current.menu.version}-a${current.revision}"`;
  const headers = { ETag: etag, "Cache-Control": "no-cache" };
  if (req.headers.get("If-None-Match") === etag) return new Response(null, { status: 304, headers });
  return new Response(JSON.stringify(current.menu), {
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}
