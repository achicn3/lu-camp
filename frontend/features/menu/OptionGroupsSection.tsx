"use client";
// 選項群組管理（docs/44 §3.2；O2）：群組（溫度、甜度、加購…）可被多個品項共用；
// 每個選項可加價、填成本、停售、設每日限量。品項要掛哪些群組在品項列的「選項與介紹」設定。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { ConfirmDialog } from "@/features/common/ConfirmDialog";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { parseNtd } from "@/lib/money";

type GroupRead = components["schemas"]["MenuOptionGroupRead"];
type OptionRead = components["schemas"]["MenuOptionRead"];
type OptionInput = components["schemas"]["MenuOptionInput"];

export const OPTION_GROUPS_QUERY_KEY = ["menu-option-groups"] as const;

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

const OPTION_LINE = /^(.*\S)\s+([+-]\d+)$/;

/** 一行一個選項；結尾空一格寫 +N／-N 是加價（必須帶正負號，「2號豆」這種名稱才不會被誤判）。 */
export function parseOptionLines(text: string): OptionInput[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((line) => {
      const match = OPTION_LINE.exec(line);
      if (match === null) return { name: line, price_delta: "0" };
      return { name: match[1], price_delta: String(Number(match[2])) };
    });
}

export function groupRuleText(group: { min_select: number; max_select: number }): string {
  if (group.min_select === 1 && group.max_select === 1) return "必選 1 項";
  if (group.min_select > 0) return `至少 ${group.min_select} 項，最多 ${group.max_select} 項`;
  return `可不選，最多 ${group.max_select} 項`;
}

function parseCount(text: string): number | null {
  return /^\d+$/.test(text.trim()) ? Number(text) : null;
}

function boundsError(min: number | null, max: number | null): string | null {
  if (min === null || max === null) return "至少選／最多選請填 0 以上的整數";
  if (max < 1) return "最多選至少要 1";
  if (min > max) return "至少選不能大於最多選";
  return null;
}

export function useOptionGroups() {
  return useQuery({
    queryKey: OPTION_GROUPS_QUERY_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/menu-option-groups");
      if (!data) throw new Error(extractDetail(error) ?? "讀取選項群組失敗");
      return data;
    },
  });
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: OPTION_GROUPS_QUERY_KEY });
    // 品項清單內嵌了群組與選項（POS 也讀它），一併重抓。
    void queryClient.invalidateQueries({ queryKey: ["menu-items"] });
  };
}

function CreateGroupForm() {
  const refresh = useRefresh();
  const [name, setName] = useState("");
  const [min, setMin] = useState("1");
  const [max, setMax] = useState("1");
  const [lines, setLines] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/menu-option-groups", {
        body: {
          name: name.trim(),
          min_select: Number(min),
          max_select: Number(max),
          sort_order: 0,
          options: parseOptionLines(lines),
        },
      });
      if (!data) throw new Error(extractDetail(error) ?? "新增群組失敗");
      return data;
    },
    onSuccess: () => {
      setName("");
      setMin("1");
      setMax("1");
      setLines("");
      setFormError(null);
      refresh();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (name.trim() === "") {
      setFormError("請輸入群組名稱");
      return;
    }
    const problem = boundsError(parseCount(min), parseCount(max));
    if (problem !== null) {
      setFormError(problem);
      return;
    }
    setFormError(null);
    create.mutate();
  }

  return (
    <form className="menu-option-create" aria-label="新增選項群組" onSubmit={submit}>
      <h3>新增選項群組</h3>
      <div className="menu-form-grid">
        <label className="field">
          <span className="field-label">群組名稱</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：溫度" />
        </label>
        <label className="field">
          <span className="field-label">至少選</span>
          <input inputMode="numeric" value={min} onChange={(e) => setMin(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">最多選</span>
          <input inputMode="numeric" value={max} onChange={(e) => setMax(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span className="field-label">選項（一行一個）</span>
        <textarea
          rows={4}
          value={lines}
          onChange={(e) => setLines(e.target.value)}
          placeholder={"熱\n冰\n燕麥奶 +20"}
        />
      </label>
      <p className="hint">
        要加價就在選項後面空一格寫「+20」；至少選 1、最多選 1＝必選一項（例如溫度），至少選 0＝可不選（例如加購）。
      </p>
      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
      <button type="submit" className="btn-primary" disabled={create.isPending}>
        {create.isPending ? "新增中…" : "新增群組"}
      </button>
    </form>
  );
}

function OptionRow({ option }: { option: OptionRead }) {
  const refresh = useRefresh();
  const [delta, setDelta] = useState(option.price_delta);
  const [cost, setCost] = useState(option.unit_cost ?? "");
  const [rowError, setRowError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const patch = useMutation({
    mutationFn: async (body: components["schemas"]["MenuOptionUpdateRequest"]) => {
      const { data, error } = await api.PATCH("/api/v1/menu-options/{option_id}", {
        params: { path: { option_id: option.id } },
        body,
      });
      if (!data) throw new Error(extractDetail(error) ?? "更新選項失敗");
      return data;
    },
    onSuccess: () => {
      setRowError(null);
      refresh();
    },
    onError: (err: Error) => setRowError(err.message),
  });

  const archive = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE("/api/v1/menu-options/{option_id}", {
        params: { path: { option_id: option.id } },
      });
      if (!response.ok) throw new Error(extractDetail(error) ?? "移除選項失敗");
    },
    onSuccess: () => {
      setConfirming(false);
      refresh();
    },
    onError: (err: Error) => {
      setConfirming(false);
      setRowError(err.message);
    },
  });

  function save() {
    const d = /^[+-]?\d+$/.test(delta.trim()) ? Number(delta) : null;
    if (d === null) {
      setRowError("加價請填整數元（可為負數，例如 -5）");
      return;
    }
    // 成本清空＝回到「沒有額外材料」（送 null）。
    const c = cost.trim() === "" ? null : parseNtd(cost);
    if (cost.trim() !== "" && (c === null || c < 0)) {
      setRowError("成本須為 0 以上的整數元");
      return;
    }
    patch.mutate({ price_delta: String(d), unit_cost: c === null ? null : String(c) });
  }

  return (
    <tr>
      <td>{option.name}</td>
      <td>
        <input
          className="pos-qty"
          inputMode="numeric"
          value={delta}
          aria-label={`${option.name} 加價`}
          onChange={(e) => setDelta(e.target.value)}
        />
      </td>
      <td>
        <input
          className="pos-qty"
          inputMode="numeric"
          value={cost}
          placeholder="—"
          aria-label={`${option.name} 成本`}
          onChange={(e) => setCost(e.target.value)}
        />
      </td>
      <td>
        <label className="menu-daily-limit">
          <input
            type="checkbox"
            checked={option.is_available}
            aria-label={`${option.name} 可售`}
            disabled={patch.isPending}
            onChange={(e) => patch.mutate({ is_available: e.target.checked })}
          />
          {option.is_available ? "可售" : "停售"}
        </label>
      </td>
      <td>
        <label className="menu-daily-limit">
          <input
            type="checkbox"
            checked={option.daily_limited}
            aria-label={`${option.name} 每日限量`}
            disabled={patch.isPending}
            onChange={(e) => patch.mutate({ daily_limited: e.target.checked })}
          />
          {option.daily_limited ? "限量" : "不限量"}
        </label>
      </td>
      <td>
        <div className="menu-row-actions">
          <button
            type="button"
            className="btn-ghost"
            aria-label={`${option.name} 儲存`}
            disabled={patch.isPending}
            onClick={save}
          >
            儲存
          </button>
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            aria-label={`${option.name} 移除`}
            onClick={() => setConfirming(true)}
          >
            移除
          </button>
        </div>
        {confirming && (
          <ConfirmDialog
            title="移除選項"
            danger
            busy={archive.isPending}
            confirmLabel="移除"
            body={
              <p>
                確定移除 <strong>{option.name}</strong>？之後點餐就看不到，已成交的紀錄不受影響。
              </p>
            }
            onConfirm={() => archive.mutate()}
            onCancel={() => setConfirming(false)}
          />
        )}
        {rowError !== null && (
          <p role="alert" className="form-error menu-row-error">
            {rowError}
          </p>
        )}
      </td>
    </tr>
  );
}

function GroupRules({ group, onClose }: { group: GroupRead; onClose: () => void }) {
  const refresh = useRefresh();
  const [name, setName] = useState(group.name);
  const [min, setMin] = useState(String(group.min_select));
  const [max, setMax] = useState(String(group.max_select));
  const [error, setError] = useState<string | null>(null);

  const patch = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.PATCH("/api/v1/menu-option-groups/{group_id}", {
        params: { path: { group_id: group.id } },
        body: { name: name.trim(), min_select: Number(min), max_select: Number(max) },
      });
      if (!data) throw new Error(extractDetail(err) ?? "更新群組失敗");
      return data;
    },
    onSuccess: () => {
      refresh();
      onClose();
    },
    onError: (err: Error) => setError(err.message),
  });

  function save() {
    if (name.trim() === "") {
      setError("請輸入群組名稱");
      return;
    }
    const problem = boundsError(parseCount(min), parseCount(max));
    if (problem !== null) {
      setError(problem);
      return;
    }
    patch.mutate();
  }

  return (
    <div className="menu-group-rules">
      <label className="field">
        <span className="field-label">名稱</span>
        <input
          value={name}
          aria-label={`${group.name} 名稱`}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">至少選</span>
        <input
          inputMode="numeric"
          value={min}
          aria-label={`${group.name} 至少選`}
          onChange={(e) => setMin(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field-label">最多選</span>
        <input
          inputMode="numeric"
          value={max}
          aria-label={`${group.name} 最多選`}
          onChange={(e) => setMax(e.target.value)}
        />
      </label>
      <button
        type="button"
        className="btn-primary"
        aria-label={`${group.name} 儲存規則`}
        disabled={patch.isPending}
        onClick={save}
      >
        儲存
      </button>
      <button type="button" className="btn-ghost" onClick={onClose}>
        取消
      </button>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  );
}

function GroupCard({ group }: { group: GroupRead }) {
  const refresh = useRefresh();
  const [editing, setEditing] = useState(false);
  const [newOption, setNewOption] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const titleId = `option-group-${group.id}`;

  const add = useMutation({
    mutationFn: async (input: OptionInput) => {
      const { data, error: err } = await api.POST(
        "/api/v1/menu-option-groups/{group_id}/options",
        { params: { path: { group_id: group.id } }, body: input },
      );
      if (!data) throw new Error(extractDetail(err) ?? "新增選項失敗");
      return data;
    },
    onSuccess: () => {
      setNewOption("");
      setError(null);
      refresh();
    },
    onError: (err: Error) => setError(err.message),
  });

  const archive = useMutation({
    mutationFn: async () => {
      const { error: err, response } = await api.DELETE(
        "/api/v1/menu-option-groups/{group_id}",
        { params: { path: { group_id: group.id } } },
      );
      if (!response.ok) throw new Error(extractDetail(err) ?? "移除群組失敗");
    },
    onSuccess: () => {
      setConfirming(false);
      refresh();
    },
    onError: (err: Error) => {
      setConfirming(false);
      setError(err.message);
    },
  });

  function addOption() {
    const [input] = parseOptionLines(newOption);
    if (input === undefined) {
      setError("請輸入選項名稱");
      return;
    }
    add.mutate(input);
  }

  return (
    <section className="card menu-group-card" aria-labelledby={titleId}>
      <header className="menu-group-head">
        <h3 id={titleId}>{group.name}</h3>
        <span className="hint">{groupRuleText(group)}</span>
        <div className="menu-row-actions">
          <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
            改規則
          </button>
          <button
            type="button"
            className="btn-ghost btn-danger-text"
            onClick={() => setConfirming(true)}
          >
            移除群組
          </button>
        </div>
      </header>
      {editing && <GroupRules group={group} onClose={() => setEditing(false)} />}
      <div className="inv-table-wrap">
        <table className="inv-table">
          <thead>
            <tr>
              <th>選項</th>
              <th>加價</th>
              <th>成本</th>
              <th>販售</th>
              <th>每日限量</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {group.options.map((option) => (
              <OptionRow key={`${option.id}-${option.price_delta}-${option.unit_cost ?? ""}`} option={option} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="menu-option-add">
        <input
          value={newOption}
          placeholder="新選項，例如：燕麥奶 +20"
          aria-label={`${group.name} 新選項`}
          onChange={(e) => setNewOption(e.target.value)}
        />
        <button
          type="button"
          className="btn-ghost"
          aria-label={`${group.name} 新增選項`}
          disabled={add.isPending}
          onClick={addOption}
        >
          新增選項
        </button>
      </div>
      {error !== null && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {confirming && (
        <ConfirmDialog
          title="移除選項群組"
          danger
          busy={archive.isPending}
          confirmLabel="移除"
          body={
            <p>
              確定移除 <strong>{group.name}</strong>？掛了這個群組的品項點餐時就不會再問，已成交的紀錄不受影響。
            </p>
          }
          onConfirm={() => archive.mutate()}
          onCancel={() => setConfirming(false)}
        />
      )}
    </section>
  );
}

export function OptionGroupsSection() {
  const query = useOptionGroups();
  return (
    <div className="menu-option-section">
      <h2>選項群組</h2>
      <p className="hint">
        溫度、甜度、加購這類選項做成群組，再到品項的「選項與介紹」掛上去；同一個群組可以給很多品項共用。
      </p>
      {query.isError && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      {(query.data ?? []).map((group) => (
        <GroupCard key={group.id} group={group} />
      ))}
      {query.isSuccess && query.data.length === 0 && <p className="hint">還沒有選項群組</p>}
      <div className="card">
        <CreateGroupForm />
      </div>
    </div>
  );
}
