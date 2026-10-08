"use client";
// POS 線上訂單（docs/44 §4.3；O4c）：客人掃桌上 QR 點的現金單。每 5 秒更新；有新單響一聲、徽章顯示
// 還沒處理的張數。客人到櫃台 → 店員按「帶入結帳」（用 POS 目前的菜單重新計價，價格變了先給店員看差額）
// → 照平常結帳收錢；結帳成立時那張線上單自動標已付款。客人沒來可以取消（保留的份數會加回）。
// 有帶回家商品的單付了錢還要「已交貨」才結單（docs/63 §13）：付款與交貨分開。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";
import { formatNtd, parseNtd } from "@/lib/money";

type Order = components["schemas"]["OnlineOrderRead"];
type Overview = components["schemas"]["OnlineOrdersRead"];
export type OnlineCart = components["schemas"]["OnlineCartRead"];

const POLL_MS = 5000;
// 現金單超過這麼久沒來付就標黃提醒（docs/44 §4.3）；第一版不自動取消。
const WAITING_WARN_MIN = 30;
export const ONLINE_ORDERS_KEY = ["online-orders"] as const;

function detail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}

function money(value: string | number): string {
  const n = typeof value === "number" ? value : parseNtd(value);
  return n === null ? "—" : `$${formatNtd(n)}`;
}

/** 還沒處理的單：已匯入、未付款、沒被庫存拒絕。 */
export function isOpen(order: Order): boolean {
  return order.sync_status === "IMPORTED" && order.payment_status === "UNPAID" && order.hold_status !== "REJECTED";
}

/** 這次新出現、還沒處理的單（第一次載入不算新，免得一開 POS 就響）。 */
/** 付了錢、帶回家商品還沒交給客人。 */
export function isAwaitingHandover(order: Order): boolean {
  return order.fulfillment_status === "AWAITING";
}

export function newOpenOrderIds(seen: Set<number> | null, orders: Order[]): number[] {
  if (seen === null) return [];
  return orders.filter((o) => isOpen(o) && !seen.has(o.id)).map((o) => o.id);
}

function chime() {
  // 短短兩聲；瀏覽器不支援或還沒互動過（自動播放限制）就算了，徽章數字照樣會變。
  try {
    const Ctx = window.AudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    [0, 0.18].forEach((at, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = i === 0 ? 880 : 1175;
      gain.gain.setValueAtTime(0.18, ctx.currentTime + at);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + at + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + at);
      osc.stop(ctx.currentTime + at + 0.17);
    });
  } catch {
    // 響不了不影響收單
  }
}

function statusLabel(order: Order): { text: string; tone: string } {
  if (isAwaitingHandover(order)) return { text: "已付款・待交貨", tone: "open" };
  if (order.fulfillment_status === "HANDED_OVER") return { text: "已交貨", tone: "done" };
  if (order.sync_status === "SETTLED") return { text: "已結帳", tone: "done" };
  if (order.sync_status === "VOIDED") return { text: "已取消", tone: "muted" };
  if (order.hold_status === "REJECTED") return { text: "庫存不足", tone: "danger" };
  if (order.hold_status === "HELD") return { text: "待付款・已保留份數", tone: "open" };
  return { text: "待付款", tone: "open" };
}

function minutesSince(iso: string): number {
  return Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
}

function placeLabel(order: Order): string {
  return order.service_mode === "TAKEOUT" ? "外帶" : `桌號 ${order.table_label ?? "—"}`;
}

export function OnlineOrdersPanel({
  cartEmpty,
  onLoad,
}: {
  /** POS 購物車是空的才能帶入（不蓋掉正在結的單）。 */
  cartEmpty: boolean;
  onLoad: (cart: OnlineCart) => void;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState<number | null>(null);
  const [priceCheck, setPriceCheck] = useState<OnlineCart | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Set<number> | null>(null);

  const query = useQuery({
    queryKey: ONLINE_ORDERS_KEY,
    refetchInterval: POLL_MS,
    queryFn: async (): Promise<Overview | null> => {
      const { data } = await api.GET("/api/v1/online-orders");
      return data ?? null;
    },
  });
  const data = query.data;
  const orders = data?.orders ?? [];

  useEffect(() => {
    if (!data) return;
    if (newOpenOrderIds(seen.current, data.orders).length > 0) chime();
    seen.current = new Set([...(seen.current ?? []), ...data.orders.map((o) => o.id)]);
  }, [data]);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ONLINE_ORDERS_KEY });

  const load = useMutation({
    mutationFn: async (orderId: number) => {
      const { data: cart, error: apiErr } = await api.GET("/api/v1/online-orders/{order_id}/cart", {
        params: { path: { order_id: orderId } },
      });
      if (!cart) throw new Error(detail(apiErr) ?? "讀不到這張線上單");
      return cart;
    },
    onSuccess: (cart) => {
      setError(null);
      if (cart.total !== cart.online_total || cart.lines.some((l) => l.unit_price !== l.online_unit_price)) {
        setPriceCheck(cart);
        return;
      }
      finishLoad(cart);
    },
    onError: (e: Error) => setError(e.message),
  });

  function finishLoad(cart: OnlineCart) {
    setPriceCheck(null);
    setOpen(false);
    onLoad(cart);
  }

  const cancel = useMutation({
    mutationFn: async (orderId: number) => {
      const { data: row, error: apiErr } = await api.POST("/api/v1/online-orders/{order_id}/cancel", {
        params: { path: { order_id: orderId } },
      });
      if (!row) throw new Error(detail(apiErr) ?? "取消失敗");
      return row;
    },
    onSuccess: () => {
      setError(null);
      setConfirmCancel(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const handOver = useMutation({
    mutationFn: async (orderId: number) => {
      const { data: row, error: apiErr } = await api.POST("/api/v1/online-orders/{order_id}/hand-over", {
        params: { path: { order_id: orderId } },
      });
      if (!row) throw new Error(detail(apiErr) ?? "交貨沒記上，請再按一次");
      return row;
    },
    onSuccess: () => {
      setError(null);
      refresh();
    },
    onError: (e: Error) => setError(e.message),
  });

  const accepting = useMutation({
    mutationFn: async (next: boolean) => {
      const { data: row, error: apiErr } = await api.PUT("/api/v1/online-orders/accepting", {
        body: { accepting: next },
      });
      if (!row) throw new Error(detail(apiErr) ?? "切換失敗，請確認網路");
      return row;
    },
    onSuccess: (row) => {
      setError(null);
      queryClient.setQueryData(ONLINE_ORDERS_KEY, row);
    },
    onError: (e: Error) => setError(e.message),
  });

  if (!data?.configured) return null;
  const openCount = orders.filter((o) => isOpen(o) || isAwaitingHandover(o)).length;
  const paused = data.accepting === false;
  const pending = (o: Order) => Number(isOpen(o) || isAwaitingHandover(o));
  const sorted = [...orders].sort((a, b) => pending(b) - pending(a));

  return (
    <>
      <button
        type="button"
        className={`btn-secondary online-orders-toggle${openCount > 0 ? " has-new" : ""}${paused ? " is-paused" : ""}`}
        onClick={() => setOpen(true)}
      >
        線上訂單
        {openCount > 0 && <span className="online-orders-badge">{openCount}</span>}
        {paused && <span className="online-orders-paused-tag">暫停中</span>}
      </button>
      {open && (
        <div className="online-orders-overlay" role="dialog" aria-modal="true" aria-labelledby="online-orders-title">
          <div className="online-orders-sheet">
            <div className="online-orders-head">
              <h2 id="online-orders-title">線上訂單</h2>
              <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
                關閉
              </button>
            </div>
            <div className={`online-orders-status${paused ? " is-paused" : ""}`}>
              <span>
                {paused ? "暫停接單中" : "接單中"}
                {paused && data.paused_reason ? `：${data.paused_reason}` : ""}
              </span>
              {data.last_pull_at && (
                <span className="hint">最後連線 {formatTaipeiDateTime(data.last_pull_at, { omitYear: true })}</span>
              )}
              <button
                type="button"
                className={paused ? "btn-primary" : "btn-secondary"}
                disabled={accepting.isPending}
                onClick={() => accepting.mutate(paused)}
              >
                {paused ? "恢復接單" : "暫停接單"}
              </button>
            </div>
            {data.last_pull_error && (
              <p role="alert" className="form-error">
                {data.last_pull_error}（新單暫時進不來，網路恢復後會自動補上）
              </p>
            )}
            {error !== null && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
            {!cartEmpty && (
              <p className="hint">要帶入線上單，請先結完或清空目前的購物車。</p>
            )}
            {sorted.length === 0 ? (
              <p className="hint">今天還沒有線上訂單。</p>
            ) : (
              <ul className="online-orders-list">
                {sorted.map((order) => {
                  const status = statusLabel(order);
                  const waited = minutesSince(order.created_at);
                  const late = isOpen(order) && waited >= WAITING_WARN_MIN;
                  return (
                    <li
                      key={order.id}
                      aria-label={`${placeLabel(order)} ${money(order.total)}`}
                      className={`online-order is-${status.tone}${late ? " is-late" : ""}`}
                    >
                      <div className="online-order-top">
                        <strong className="online-order-place">{placeLabel(order)}</strong>
                        <span className={`online-order-status tone-${status.tone}`}>{status.text}</span>
                        <span className="hint">
                          {formatTaipeiDateTime(order.created_at, { omitYear: true })}
                          {isOpen(order) ? `・已等 ${waited} 分鐘` : ""}
                        </span>
                        <strong className="online-order-total money">{money(order.total)}</strong>
                      </div>
                      <ul className="online-order-lines">
                        {order.lines.map((line) => (
                          <li key={line.line_no}>
                            {line.name} ×{line.qty}
                            {line.catalog_product_id != null ? "（帶回家）" : ""}
                          </li>
                        ))}
                      </ul>
                      {order.note && <p className="online-order-note">備註：{order.note}</p>}
                      {order.hold_status === "REJECTED" && order.reject_reason && (
                        <p className="form-error">庫存不足：{order.reject_reason}（客人那邊已顯示，請客人重新點）</p>
                      )}
                      {isAwaitingHandover(order) && (order.handover_items ?? []).length > 0 && (
                        <p className="online-order-handover">
                          要交給客人：
                          {(order.handover_items ?? []).map((item) => `${item.name} ×${item.qty}`).join("、")}
                        </p>
                      )}
                      {isAwaitingHandover(order) && (
                        <div className="online-order-actions">
                          <span>把帶回家的商品交給客人後再按。</span>
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={handOver.isPending}
                            onClick={() => handOver.mutate(order.id)}
                          >
                            已交貨
                          </button>
                        </div>
                      )}
                      {isOpen(order) && (
                        <div className="online-order-actions">
                          {confirmCancel === order.id ? (
                            <>
                              <span>客人沒來或點錯？取消後保留的份數會加回。</span>
                              <button
                                type="button"
                                className="btn-danger"
                                disabled={cancel.isPending}
                                onClick={() => cancel.mutate(order.id)}
                              >
                                確定取消
                              </button>
                              <button type="button" className="btn-ghost" onClick={() => setConfirmCancel(null)}>
                                不取消
                              </button>
                            </>
                          ) : (
                            <>
                              <button type="button" className="btn-ghost" onClick={() => setConfirmCancel(order.id)}>
                                取消這張
                              </button>
                              <button
                                type="button"
                                className="btn-primary"
                                disabled={!cartEmpty || load.isPending}
                                onClick={() => load.mutate(order.id)}
                              >
                                帶入結帳
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          {priceCheck && (
            <div className="online-orders-price" role="alertdialog" aria-modal="true" aria-labelledby="online-price-title">
              <h3 id="online-price-title">價格有變動</h3>
              <p>客人點餐後菜單改過價，結帳會照現在的價格收：</p>
              <ul>
                {priceCheck.lines
                  .filter((l) => l.unit_price !== l.online_unit_price)
                  .map((l) => (
                    <li key={l.line_no}>
                      {l.description}：客人看到 {money(l.online_unit_price)} → 現在 {money(l.unit_price)}
                    </li>
                  ))}
              </ul>
              <p>
                合計 客人看到 <strong>{money(priceCheck.online_total)}</strong> → 現在 <strong>{money(priceCheck.total)}</strong>
                ，請先跟客人說明。
              </p>
              <div className="online-orders-price-actions">
                <button type="button" className="btn-ghost" onClick={() => setPriceCheck(null)}>
                  先不要
                </button>
                <button type="button" className="btn-primary" onClick={() => finishLoad(priceCheck)}>
                  照現在的價格帶入
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
