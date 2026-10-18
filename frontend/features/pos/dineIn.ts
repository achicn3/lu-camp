// POS 內用/外帶與桌號的純邏輯（docs/35）。與金額完全無關——這兩個欄位不進入任何
// 金額、稅、折扣、點數計算，只決定出餐單印什麼、以及結帳能不能送出。
//
// 後端有同一組守衛（sales service + DB CHECK）；此處是**先行**擋下，讓店員在按下
// 結帳前就看到原因，而不是送出去才吃 422。

export type ServiceMode = "DINE_IN" | "TAKEOUT";

export interface DineInSelection {
  mode: ServiceMode | null;
  tableNo: string | null;
}

export interface DineInValidation {
  /** 購物車含餐飲行 → 必須選內用/外帶。 */
  required: boolean;
  ok: boolean;
  /** 不可結帳時的中文原因（顯示於收款區）。 */
  error: string | null;
  /** 桌號清單是空的：內用整個不可選，提示要去設定頁維護。 */
  tablesUnavailable: boolean;
}

const ok = (required: boolean): DineInValidation => ({
  required,
  ok: true,
  error: null,
  tablesUnavailable: false,
});

/** 驗證選擇是否可結帳。`tables` 來自 settings.dine_in_tables。 */
export function validateDineIn(
  hasMenuLine: boolean,
  selection: DineInSelection,
  tables: string[],
): DineInValidation {
  if (!hasMenuLine) return ok(false);
  const tablesUnavailable = tables.length === 0;
  if (selection.mode === null) {
    return {
      required: true,
      ok: false,
      // 桌號清單是空的時，內用鍵在畫面上是**停用**的——店員按不下去，也就永遠看不到
      // 後面那句「請至設定頁維護桌號」。所以一開始就要把設定指引講出來。
      error: tablesUnavailable
        ? "本單含餐飲：可選外帶；要點內用請先至設定頁維護桌號清單"
        : "本單含餐飲，請先選擇內用或外帶",
      tablesUnavailable,
    };
  }
  if (selection.mode === "TAKEOUT") return { ...ok(true), tablesUnavailable };
  if (tablesUnavailable) {
    return {
      required: true,
      ok: false,
      error: "尚未設定桌號清單，請先至設定頁維護後才能點內用",
      tablesUnavailable: true,
    };
  }
  // 選過桌號之後管理者才把它從清單移除的話，畫面上的選擇會變成孤兒；在這裡擋下
  // 比送到後端吃 422 早，店員看得到「重選一桌」而不是一句失敗訊息。
  if (selection.tableNo === null || !tables.includes(selection.tableNo)) {
    return {
      required: true,
      ok: false,
      error: "請選擇桌號",
      tablesUnavailable: false,
    };
  }
  return ok(true);
}

/** 結帳 body 要帶的欄位。沒有餐飲行時回空物件——多帶欄位後端會 422。 */
export function dineInRequestFields(
  hasMenuLine: boolean,
  selection: DineInSelection,
): { service_mode?: ServiceMode; table_no?: string } {
  if (!hasMenuLine || selection.mode === null) return {};
  if (selection.mode === "TAKEOUT") return { service_mode: "TAKEOUT" };
  return {
    service_mode: "DINE_IN",
    ...(selection.tableNo === null ? {} : { table_no: selection.tableNo }),
  };
}

/** 購物車不再有餐飲行（或開始下一筆）時重置。 */
export function clearDineIn(): DineInSelection {
  return { mode: null, tableNo: null };
}

/** 購物車裡的一行續點（品項 id＋顯示名稱）。 */
export interface RefillLine {
  menuItemId: number;
  description: string;
}

/**
 * 續點檢查（店主 2026-10-19）：外帶不能續點（擋下，後端同一條）。內用選好桌號後顯示本桌 90 分鐘內
 * 有沒有點過：有就確認（found）、沒有只提醒（warning），照樣能結（櫃檯常有換桌、桌號沒填的狀況）。
 * `recentIds`＝本桌最近點過的品項，還沒查到是 null（先不說）。
 */
export function refillCheck(
  refills: RefillLine[],
  selection: DineInSelection,
  recentIds: readonly number[] | null,
): { error: string | null; warning: string | null; found: string | null } {
  const none = { error: null, warning: null, found: null };
  if (refills.length === 0) return none;
  if (selection.mode === "TAKEOUT") {
    return { ...none, error: "外帶不能續點，請把續點改成一般點，或改選內用" };
  }
  if (selection.mode !== "DINE_IN" || selection.tableNo === null || recentIds === null) return none;
  const names = (lines: RefillLine[]) => lines.map((line) => line.description).join("、");
  const ordered = refills.filter((line) => recentIds.includes(line.menuItemId));
  const missing = refills.filter((line) => !recentIds.includes(line.menuItemId));
  return {
    error: null,
    warning: missing.length > 0 ? `本桌 90 分鐘內沒點過：${names(missing)}。確定是續點再結帳。` : null,
    found: ordered.length > 0 ? `本桌 90 分鐘內點過：${names(ordered)}` : null,
  };
}
