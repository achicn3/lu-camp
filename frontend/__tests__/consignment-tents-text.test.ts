import { describe, expect, it } from "vitest";

import {
  WEB_RESERVED_NOTE,
  linesToList,
  listToLines,
  missingInfo,
  withWebReservedNote,
} from "@/features/consignment-tents/tentText";
import type { components } from "@/lib/api-types";

type Tent = components["schemas"]["TentListingRead"];

function tent(overrides: Partial<Tent> = {}): Tent {
  return {
    item_id: 1,
    item_code: "T-1",
    name: "Amenity Dome M",
    brand_name: "Snow Peak",
    listed_price: "6800",
    published: false,
    reserved: false,
    color: "象牙白",
    nights: 6,
    missing: [],
    defects: [],
    extras: [],
    video_url: null,
    photos: ["a".repeat(64)],
    updated_at: null,
    ...overrides,
  };
}

describe("一行一項", () => {
  it("去掉空行與頭尾空白", () => {
    expect(linesToList("  營釘少 4 支 \n\n  \r\n少一條營繩")).toEqual(["營釘少 4 支", "少一條營繩"]);
  });
  it("來回轉換不變", () => {
    expect(linesToList(listToLines(["原廠地墊", "原廠地布"]))).toEqual(["原廠地墊", "原廠地布"]);
  });
});

describe("資料齊不齊", () => {
  it("齊全就沒有提醒", () => {
    expect(missingInfo(tent())).toEqual([]);
  });
  it("列出缺的項目；露數 0 不算缺", () => {
    expect(missingInfo(tent({ photos: [], nights: null, color: null }))).toEqual([
      "沒照片",
      "沒填露數",
      "沒填顏色",
    ]);
    expect(missingInfo(tent({ nights: 0 }))).toEqual([]);
  });
});

describe("POS 保留提醒", () => {
  it("沒保留：原備註照舊", () => {
    expect(withWebReservedNote("附收納袋", false)).toBe("附收納袋");
    expect(withWebReservedNote(null, false)).toBeNull();
    expect(withWebReservedNote("  ", false)).toBeNull();
  });
  it("保留中：提醒放最前面，原備註接在後面", () => {
    expect(withWebReservedNote(null, true)).toBe(WEB_RESERVED_NOTE);
    expect(withWebReservedNote("附收納袋", true)).toBe(`${WEB_RESERVED_NOTE}；附收納袋`);
  });
});
