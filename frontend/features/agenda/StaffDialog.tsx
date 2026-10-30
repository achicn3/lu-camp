"use client";
// 員工名單（docs/99 §4.4，限管理者）：待辦的負責人從這裡選，第三期排班也用同一份。
// 員工不一定要有系統帳號（工讀生也能加）。離職按「停用」，以前指派給他的待辦仍看得到名字。
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { errorMessage, useStaffMembers } from "@/features/agenda/api";
import type { StaffMember } from "@/features/agenda/calendar";
import { DialogShell } from "@/features/agenda/DialogShell";
import { api } from "@/lib/api";

function StaffRow({
  member,
  busy,
  onSave,
}: {
  member: StaffMember;
  busy: boolean;
  onSave: (id: number, body: { name?: string; is_active?: boolean }) => void;
}) {
  const [name, setName] = useState(member.name);
  const renamed = name.trim() !== "" && name.trim() !== member.name;
  return (
    <li className={`agenda-staff-row${member.is_active ? "" : " agenda-staff-inactive"}`}>
      <input
        aria-label={`${member.name} 的姓名`}
        value={name}
        maxLength={50}
        onChange={(e) => setName(e.target.value)}
      />
      {renamed && (
        <button
          type="button"
          className="btn-secondary"
          disabled={busy}
          onClick={() => onSave(member.id, { name: name.trim() })}
        >
          改名
        </button>
      )}
      <button
        type="button"
        className="btn-ghost"
        disabled={busy}
        onClick={() => onSave(member.id, { is_active: !member.is_active })}
      >
        {member.is_active ? "停用" : "復職"}
      </button>
    </li>
  );
}

export function StaffDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const members = useStaffMembers(true);
  const [newName, setNewName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["staff-members"] });

  const create = useMutation({
    mutationFn: async (name: string) => {
      const { data, error } = await api.POST("/api/v1/staff-members", { body: { name } });
      if (!data) throw new Error(errorMessage(error, "新增失敗"));
    },
    onSuccess: async () => {
      setNewName("");
      await refresh();
    },
    onError: (err) => setFormError(err.message),
  });
  const update = useMutation({
    mutationFn: async ({ id, body }: { id: number; body: { name?: string; is_active?: boolean } }) => {
      const { data, error } = await api.PATCH("/api/v1/staff-members/{member_id}", {
        params: { path: { member_id: id } },
        body,
      });
      if (!data) throw new Error(errorMessage(error, "更新失敗"));
    },
    onSuccess: refresh,
    onError: (err) => setFormError(err.message),
  });
  const busy = create.isPending || update.isPending;

  return (
    <DialogShell title="員工名單" busy={busy} onClose={onClose}>
      <p className="hint">待辦可以指定給這裡的人。離職的人按「停用」，以前的待辦還是看得到名字。</p>
      {members.isError && (
        <p role="alert" className="form-error">
          {members.error.message}
        </p>
      )}
      <ul className="agenda-staff-list">
        {(members.data ?? []).map((m) => (
          <StaffRow
            key={m.id}
            member={m}
            busy={busy}
            onSave={(id, body) => {
              setFormError(null);
              update.mutate({ id, body });
            }}
          />
        ))}
      </ul>
      {members.data?.length === 0 && <p className="hint">還沒有員工。</p>}
      <form
        className="agenda-staff-add"
        onSubmit={(e) => {
          e.preventDefault();
          setFormError(null);
          if (newName.trim() === "") {
            setFormError("請填姓名");
            return;
          }
          create.mutate(newName.trim());
        }}
      >
        <input
          aria-label="新員工姓名"
          placeholder="新員工姓名"
          value={newName}
          maxLength={50}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button type="submit" className="btn-primary" disabled={busy}>
          新增
        </button>
      </form>
      {formError !== null && (
        <p role="alert" className="form-error">
          {formError}
        </p>
      )}
    </DialogShell>
  );
}
