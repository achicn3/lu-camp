"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import type { CartLine } from "@/features/pos/cart";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";
import { newIdempotencyKey } from "@/lib/uuid";

type SaleLine = components["schemas"]["SaleLineCreateRequest"];
type Tender = components["schemas"]["CartTenderRequest"];
type StaffCart = components["schemas"]["StaffCartSessionRead"];
type CartItem = components["schemas"]["CartItemRead"];
type Terminal = components["schemas"]["TerminalRead"];

const TERMINAL_INSTALLATION_KEY = "lu-camp.pos-terminal.installation";

function terminalInstallationId(): string {
  const existing = window.localStorage.getItem(TERMINAL_INSTALLATION_KEY);
  if (existing) return existing;
  const generated = newIdempotencyKey();
  window.localStorage.setItem(TERMINAL_INSTALLATION_KEY, generated);
  return generated;
}

function itemIdentity(item: CartItem): Partial<CartLine> | null {
  const separator = item.item_key.indexOf(":");
  if (separator < 1) return null;
  const raw = item.item_key.slice(separator + 1);
  switch (item.line_type) {
    case "SERIALIZED":
      return { key: `S:${raw}`, itemCode: raw, maxQty: 1 };
    case "CATALOG": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `C:${id}`, catalogProductId: id }
        : null;
    }
    case "BULK_LOT": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `B:${id}`, bulkLotId: id }
        : null;
    }
    case "MENU": {
      const id = Number(raw);
      return Number.isInteger(id) && id > 0
        ? { key: `M:${id}`, menuItemId: id }
        : null;
    }
  }
}

/** POS 重整時只以後端快照重建顯示；價格仍會在下一次 quote／sync 由後端重算。 */
export function restoreLines(items: CartItem[]): CartLine[] {
  return items.flatMap((item) => {
    const identity = itemIdentity(item);
    if (!identity?.key) return [];
    return [
      {
        ...identity,
        key: identity.key,
        lineType: item.line_type,
        description: item.name,
        unitPrice: parseNtd(item.unit_price) ?? 0,
        qty: item.qty,
      },
    ];
  });
}

interface PosCustomerDisplayProps {
  lines: SaleLine[];
  buyerContactId: number | null;
  tenders: Tender[];
  ready: boolean;
  onRestore: (cart: StaffCart) => void | Promise<void>;
}

type PendingSync =
  | {
      kind: "UPSERT";
      lines: SaleLine[];
      buyerContactId: number | null;
      tenders: Tender[];
    }
  | { kind: "CANCEL" };

export function PosCustomerDisplay({
  lines,
  buyerContactId,
  tenders,
  ready,
  onRestore,
}: PosCustomerDisplayProps) {
  const queryClient = useQueryClient();
  const [pairingCode, setPairingCode] = useState("");
  const [syncError, setSyncError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [syncedRevision, setSyncedRevision] = useState<number | null>(null);
  const payloadFingerprint = JSON.stringify({ lines, buyerContactId, tenders });
  const payload = useRef({ lines, buyerContactId, tenders });
  const revision = useRef<number | null>(null);
  const pending = useRef<PendingSync | null>(null);
  const draining = useRef(false);
  const hydratedTerminal = useRef<number | null>(null);

  const terminal = useQuery({
    queryKey: ["customer-display", "terminal"],
    retry: false,
    refetchInterval: 15_000,
    queryFn: async () => {
      const { data, error } = await api.POST("/api/v1/customer-display/terminals", {
        body: {
          installation_id: terminalInstallationId(),
          name: "主要櫃檯",
        },
      });
      if (!data) {
        const detail =
          error && typeof error === "object" && "detail" in error
            ? String(error.detail)
            : "無法註冊 POS 櫃檯";
        throw new Error(detail);
      }
      return data;
    },
  });
  const current = useQuery({
    queryKey: ["customer-display", "cart", terminal.data?.id],
    enabled: terminal.data?.paired_kiosk != null,
    retry: false,
    queryFn: async () => {
      if (!terminal.data) return null;
      const { data, response } = await api.GET(
        "/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        { params: { path: { terminal_id: terminal.data.id } } },
      );
      if (!response.ok) throw new Error("無法恢復客顯購物車");
      return data ?? null;
    },
  });

  useEffect(() => {
    payload.current = { lines, buyerContactId, tenders };
  }, [buyerContactId, lines, tenders]);

  useEffect(() => {
    const terminalId = terminal.data?.id ?? null;
    if (terminalId !== hydratedTerminal.current) {
      hydratedTerminal.current = terminalId;
      revision.current = null;
      setSyncedRevision(null);
      setHydrated(false);
    }
  }, [terminal.data?.id]);

  useEffect(() => {
    if (!current.isSuccess || hydrated || terminal.data == null) return;
    revision.current = current.data?.revision ?? null;
    setSyncedRevision(revision.current);
    let active = true;
    void Promise.resolve(current.data ? onRestore(current.data) : undefined).then(() => {
      if (active) setHydrated(true);
    });
    return () => {
      active = false;
    };
  }, [current.data, current.isSuccess, hydrated, onRestore, terminal.data]);

  async function drain(terminalRow: Terminal) {
    if (draining.current) return;
    draining.current = true;
    try {
      while (pending.current) {
        const next = pending.current;
        pending.current = null;
        if (next.kind === "UPSERT") {
          const { data, response } = await api.PUT(
            "/api/v1/customer-display/terminals/{terminal_id}/cart",
            {
              params: { path: { terminal_id: terminalRow.id } },
              body: {
                expected_revision: revision.current,
                lines: next.lines,
                buyer_contact_id: next.buyerContactId,
                tenders: next.tenders.length > 0 ? next.tenders : null,
              },
            },
          );
          if (!data) {
            throw new Error(
              response.status === 409
                ? "客顯購物車版本衝突，請重新整理 POS 後再操作。"
                : "客顯同步失敗，請確認店內網路。",
            );
          }
          revision.current = data.revision;
          setSyncedRevision(data.revision);
        } else if (revision.current !== null) {
          const { data } = await api.POST(
            "/api/v1/customer-display/terminals/{terminal_id}/cart/cancel",
            {
              params: { path: { terminal_id: terminalRow.id } },
              body: {
                expected_revision: revision.current,
                reason: "店員清空購物車",
              },
            },
          );
          if (!data) throw new Error("客顯清場失敗，請重新整理 POS。");
          revision.current = null;
          setSyncedRevision(null);
        }
        setSyncError(null);
      }
    } catch (error) {
      pending.current = null;
      setSyncError(error instanceof Error ? error.message : "客顯同步失敗");
    } finally {
      draining.current = false;
    }
  }

  useEffect(() => {
    const terminalRow = terminal.data;
    if (
      !hydrated ||
      !terminalRow?.paired_kiosk ||
      !ready ||
      syncError !== null
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      const latest = payload.current;
      pending.current =
        latest.lines.length > 0
          ? {
              kind: "UPSERT",
              lines: latest.lines,
              buyerContactId: latest.buyerContactId,
              tenders: latest.tenders,
            }
          : { kind: "CANCEL" };
      void drain(terminalRow);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [
    buyerContactId,
    hydrated,
    payloadFingerprint,
    ready,
    syncError,
    terminal.data,
  ]);

  const pair = useMutation({
    mutationFn: async (code: string) => {
      if (!terminal.data) throw new Error("POS 櫃檯尚未就緒");
      const { data, error } = await api.POST(
        "/api/v1/customer-display/terminals/{terminal_id}/pair",
        {
          params: { path: { terminal_id: terminal.data.id } },
          body: { pairing_code: code },
        },
      );
      if (!data) {
        const detail =
          error && typeof error === "object" && "detail" in error
            ? String(error.detail)
            : "配對失敗";
        throw new Error(detail);
      }
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["customer-display", "terminal"], data);
      setPairingCode("");
    },
  });

  function submitPair(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (/^\d{6}$/.test(pairingCode)) pair.mutate(pairingCode);
  }

  if (terminal.isPending) {
    return <div className="pos-kiosk-status is-muted">客顯設定載入中…</div>;
  }
  if (terminal.isError || !terminal.data) {
    return (
      <div className="pos-kiosk-status is-error" role="alert">
        客顯設定無法連線；一般付款仍可繼續，購物金暫不可用。
      </div>
    );
  }
  if (!terminal.data.paired_kiosk) {
    return (
      <form className="pos-kiosk-status is-warning" onSubmit={submitPair}>
        <strong>客顯尚未配對</strong>
        <label>
          <span className="sr-only">客顯配對碼</span>
          <input
            inputMode="numeric"
            pattern="\d{6}"
            maxLength={6}
            placeholder="輸入 6 碼"
            value={pairingCode}
            onChange={(event) =>
              setPairingCode(event.target.value.replace(/\D/g, "").slice(0, 6))
            }
          />
        </label>
        <button type="submit" className="btn-secondary" disabled={pair.isPending}>
          {pair.isPending ? "配對中…" : "配對"}
        </button>
        {pair.isError && <span role="alert">{pair.error.message}</span>}
      </form>
    );
  }
  const kiosk = terminal.data.paired_kiosk;
  return (
    <div className={`pos-kiosk-status ${kiosk.online ? "is-online" : "is-warning"}`}>
      <span className="pos-kiosk-dot" aria-hidden />
      <strong>{kiosk.online ? "客顯已連線" : "客顯離線"}</strong>
      <span>{kiosk.label}</span>
      {syncedRevision !== null && <span>購物車版本 {syncedRevision}</span>}
      {syncError && (
        <span role="alert" className="form-error">
          {syncError}
        </span>
      )}
    </div>
  );
}
