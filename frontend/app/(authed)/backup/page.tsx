"use client";
// /backup 備份儀表板（docs/31 §5/§6，MANAGER）：健康度、設定（間隔/保留/離峰/啟用）、
// 備份清單、手動觸發（背景執行＋輪詢）、卡控還原（還原到驗證庫＋四驗，切換另跑受控腳本）。
// 權限以 MANAGER-only 的 /backup/health 之 401/403 把關。
import "./backup.css";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { formatTaipeiDateTime } from "@/lib/datetime";

type BackupHealthRead = components["schemas"]["BackupHealthRead"];
type BackupRunRead = components["schemas"]["BackupRunRead"];
type RestoreRunRead = components["schemas"]["RestoreRunRead"];

/** MANAGER-only 端點回 401/403 → 標記無權限（與一般讀取失敗區分）。 */
class ForbiddenError extends Error {}

function extractDetail(error: unknown): string | null {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

function formatDateTime(iso: string | null | undefined): string {
  return formatTaipeiDateTime(iso);
}

function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function StatusBadge({ status }: { status: BackupRunRead["status"] }) {
  const cls =
    status === "SUCCEEDED"
      ? "backup-status--succeeded"
      : status === "RUNNING"
        ? "backup-status--running"
        : "backup-status--failed";
  const label = status === "SUCCEEDED" ? "成功" : status === "RUNNING" ? "進行中" : "失敗";
  return <span className={`backup-status ${cls}`}>{label}</span>;
}

function HealthCard({
  health,
  onTriggered,
}: {
  health: BackupHealthRead;
  onTriggered: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const trigger = useMutation({
    mutationFn: async () => {
      const { data, error: apiError, response } = await api.POST("/api/v1/backup/runs", {});
      if (response.status === 503) {
        throw new Error(extractDetail(apiError) ?? "備份尚未設定（R2 憑證未提供）");
      }
      if (response.status === 409) {
        throw new Error(extractDetail(apiError) ?? "已有一筆備份進行中");
      }
      if (!data) throw new Error(extractDetail(apiError) ?? "觸發備份失敗");
      return data;
    },
    onSuccess: () => {
      setError(null);
      onTriggered();
    },
    onError: (err: Error) => setError(err.message),
  });

  const age =
    health.last_success_age_hours == null
      ? "從未成功備份"
      : health.last_success_age_hours < 24
        ? `${health.last_success_age_hours.toFixed(1)} 小時前`
        : `${(health.last_success_age_hours / 24).toFixed(1)} 天前`;
  // 落後告警：從未成功、或落後超過「間隔 × 2」視為需注意。
  const stale =
    health.last_success_age_hours == null ||
    health.last_success_age_hours > health.interval_hours * 2;

  return (
    <div className="card">
      <h2>備份健康度</h2>
      <div className="backup-key-warning">
        <strong>兩組金鑰缺一即廢：</strong>備份檔以 AES 口令加密，另需 repo 的{" "}
        <code>PII_ENC_KEY / HMAC_KEY / SECRET_KEY</code> 才能還原並解出加密個資。
        請將 <code>.env.r2</code> 的 AES 口令與這三把金鑰<strong>同時抄存於店外安全處</strong>
        ——任一遺失，備份將無法還原。
      </div>
      <div className="backup-health-grid" style={{ marginTop: 12 }}>
        <div className="backup-health-item">
          <div className="label">自動備份</div>
          <div className="value">{health.enabled ? "已啟用" : "已停用"}</div>
        </div>
        <div className="backup-health-item">
          <div className="label">上次成功</div>
          <div className="value" style={{ color: stale ? "#b91c1c" : undefined }}>
            {age}
          </div>
        </div>
        <div className="backup-health-item">
          <div className="label">上次成功時間</div>
          <div className="value">{formatDateTime(health.last_success_at)}</div>
        </div>
        <div className="backup-health-item">
          <div className="label">狀態</div>
          <div className="value">
            {health.running ? "備份進行中…" : health.due_now ? "已到期，待備份" : "最新"}
          </div>
        </div>
      </div>
      {stale && (
        <p role="alert" className="form-error" style={{ marginTop: 12 }}>
          ⚠️ 備份已落後，請確認排程或立即手動備份。
        </p>
      )}
      <div style={{ marginTop: 16 }}>
        <button
          type="button"
          className="btn-primary"
          disabled={trigger.isPending || health.running}
          onClick={() => trigger.mutate()}
        >
          {trigger.isPending ? "觸發中…" : health.running ? "備份進行中…" : "立即備份"}
        </button>
      </div>
      {error && (
        <p role="alert" className="form-error" style={{ marginTop: 8 }}>
          {error}
        </p>
      )}
    </div>
  );
}

function BackupSettingsCard({
  health,
  onSaved,
}: {
  health: BackupHealthRead;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const mutation = useMutation({
    mutationFn: async (body: components["schemas"]["SettingsUpdateRequest"]) => {
      const { data, error: apiError } = await api.PATCH("/api/v1/settings", { body });
      if (!data) throw new Error(extractDetail(apiError) ?? "儲存失敗");
      return data;
    },
    onSuccess: () => {
      setSuccess(true);
      setError(null);
      onSaved();
    },
    onError: (err: Error) => {
      setError(err.message);
      setSuccess(false);
    },
  });

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    const form = new FormData(event.currentTarget);
    mutation.mutate({
      backup_enabled: form.get("backup_enabled") === "on",
      backup_interval_hours: Number(form.get("backup_interval_hours")),
      backup_retention: Number(form.get("backup_retention")),
      backup_offpeak_hour: Number(form.get("backup_offpeak_hour")),
    });
  }

  return (
    <div className="card">
      <h2>備份設定</h2>
      <form onSubmit={onSubmit}>
        <label className="field">
          <span className="field-label">啟用自動備份</span>
          <input type="checkbox" name="backup_enabled" defaultChecked={health.enabled} />
        </label>
        <label className="field">
          <span className="field-label">備份間隔（小時）</span>
          <input
            type="number"
            name="backup_interval_hours"
            min={1}
            max={8760}
            defaultValue={health.interval_hours}
          />
        </label>
        <label className="field">
          <span className="field-label">保留份數</span>
          <input
            type="number"
            name="backup_retention"
            min={1}
            max={365}
            defaultValue={health.retention}
          />
        </label>
        <label className="field">
          <span className="field-label">離峰時點（0–23 時；打烊後備份）</span>
          <input
            type="number"
            name="backup_offpeak_hour"
            min={0}
            max={23}
            defaultValue={health.offpeak_hour}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={mutation.isPending}>
          {mutation.isPending ? "儲存中…" : "儲存設定"}
        </button>
        {success && <p className="form-success">已儲存</p>}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}

function RunsCard({ runs }: { runs: BackupRunRead[] }) {
  return (
    <div className="card">
      <h2>備份紀錄</h2>
      {runs.length === 0 ? (
        <p className="hint">尚無備份紀錄。</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>開始時間</th>
              <th>觸發</th>
              <th>狀態</th>
              <th>大小</th>
              <th>SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{formatDateTime(run.started_at)}</td>
                <td>{run.trigger === "MANUAL" ? "手動" : "排程"}</td>
                <td>
                  <StatusBadge status={run.status} />
                  {run.status === "FAILED" && run.last_error && (
                    <div className="backup-error-cell">{run.last_error}</div>
                  )}
                </td>
                <td>{formatSize(run.size_bytes)}</td>
                <td className="backup-sha">{run.sha256 ? `${run.sha256.slice(0, 12)}…` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function RestoreStatusBadge({ status }: { status: RestoreRunRead["status"] }) {
  const cls =
    status === "VERIFIED"
      ? "backup-status--succeeded"
      : status === "RUNNING"
        ? "backup-status--running"
        : "backup-status--failed";
  const label = status === "VERIFIED" ? "四驗通過" : status === "RUNNING" ? "還原中" : "失敗";
  return <span className={`backup-status ${cls}`}>{label}</span>;
}

function VerificationList({ v }: { v: RestoreRunRead["verifications"] }) {
  if (!v || !Array.isArray(v.checks)) return null;
  return (
    <ul className="backup-verify-list">
      {(v.checks as { name: string; ok: boolean; detail: string }[]).map((c) => (
        <li key={c.name}>
          <span>{c.ok ? "✅" : "❌"}</span> <strong>{c.name}</strong>：{c.detail}
        </li>
      ))}
    </ul>
  );
}

function RestoreCard({
  backups,
  restores,
  retention,
  onTriggered,
}: {
  backups: BackupRunRead[];
  restores: RestoreRunRead[];
  retention: number;
  onTriggered: () => void;
}) {
  // 可還原來源＝成功且有 r2_key 的備份，且**只列最新 retention 份**（＝修剪保留的那些）。
  // 更舊的雖仍有 SUCCEEDED 列，其 R2 物件已被保留份數修剪刪除，列出會讓人選到不存在的復原點
  // （選了要等還原啟動才失敗）。只列保留中的份數＝fail-closed（Codex 第五輪 #3）。
  const sources = backups
    .filter((b) => b.status === "SUCCEEDED" && b.r2_key)
    .slice(0, Math.max(retention, 0));
  const [selected, setSelected] = useState<string>("");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosen = sources.find((b) => b.r2_key === selected) ?? null;
  const fileName = chosen?.file_name ?? "";

  const restore = useMutation({
    mutationFn: async () => {
      const { data, error: apiError, response } = await api.POST("/api/v1/backup/restore", {
        body: { source_r2_key: selected, confirm_text: typed.trim(), acknowledge: ack },
      });
      if (response.status === 503) {
        throw new Error(extractDetail(apiError) ?? "還原尚未設定（R2 憑證未提供）");
      }
      if (response.status === 400) {
        throw new Error(extractDetail(apiError) ?? "確認未通過");
      }
      if (!data) throw new Error(extractDetail(apiError) ?? "觸發還原失敗");
      return data;
    },
    onSuccess: () => {
      setConfirming(false);
      setTyped("");
      setAck(false);
      setError(null);
      onTriggered();
    },
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="card">
      <h2>還原（災難復原）</h2>
      <p className="hint">
        還原一律倒進**獨立的驗證庫**（<code>lucamp_restore_&lt;時戳&gt;</code>）並自動四驗，
        <strong>不影響現行營運資料</strong>。四驗通過後，正式切換需由店家停機執行受控腳本
        <code>scripts/switch-to-restore.sh</code>（App 不會自動切換，避免中途失敗兩頭落空）。
      </p>
      {sources.length === 0 ? (
        <p className="hint">尚無可還原的備份（需先有一次成功備份）。</p>
      ) : (
        <div className="field">
          <span className="field-label">選擇要還原的備份</span>
          <select value={selected} onChange={(e) => setSelected(e.target.value)}>
            <option value="">— 請選擇 —</option>
            {sources.map((b) => (
              <option key={b.id} value={b.r2_key ?? ""}>
                {b.file_name}（{formatDateTime(b.started_at)}，{formatSize(b.size_bytes)}）
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn-primary"
            style={{ marginTop: 8 }}
            disabled={!selected}
            onClick={() => {
              setConfirming(true);
              setError(null);
            }}
          >
            還原此備份到驗證庫…
          </button>
        </div>
      )}

      {confirming && chosen && (
        <div className="backup-confirm">
          <p className="form-error">
            ⚠️ 高風險操作。將把「{fileName}」還原到獨立驗證庫並四驗。請輸入檔名並勾選確認。
          </p>
          <label className="field">
            <span className="field-label">輸入備份檔名以確認：{fileName}</span>
            <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
          <label className="field-inline">
            <input
              type="checkbox"
              aria-label="知情同意"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />
            <span>
              我了解此還原倒進獨立驗證庫、不影響現行資料，正式切換需另跑受控停機腳本。
            </span>
          </label>
          <div className="backup-confirm-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={restore.isPending || typed.trim() !== fileName || !ack}
              onClick={() => restore.mutate()}
            >
              {restore.isPending ? "觸發中…" : "確認還原到驗證庫"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setConfirming(false);
                setTyped("");
                setAck(false);
              }}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="form-error" style={{ marginTop: 8 }}>
          {error}
        </p>
      )}

      {restores.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>還原紀錄</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>時間</th>
                <th>來源</th>
                <th>驗證庫</th>
                <th>狀態</th>
              </tr>
            </thead>
            <tbody>
              {restores.map((r) => (
                <tr key={r.id}>
                  <td>{formatDateTime(r.started_at)}</td>
                  <td className="backup-sha">{r.source_r2_key.replace("backups/", "")}</td>
                  <td className="backup-sha">{r.restore_db_name}</td>
                  <td>
                    <RestoreStatusBadge status={r.status} />
                    {r.status === "VERIFIED" && (
                      <div className="backup-verify-ok">
                        四驗通過，可切換（停機跑 switch-to-restore.sh {r.restore_db_name}）。
                      </div>
                    )}
                    {r.status === "FAILED" && r.last_error && (
                      <div className="backup-error-cell">{r.last_error}</div>
                    )}
                    <VerificationList v={r.verifications} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

export default function BackupPage() {
  const queryClient = useQueryClient();

  const healthQuery = useQuery({
    queryKey: ["backup-health"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/backup/health");
      if (response.status === 401 || response.status === 403) throw new ForbiddenError();
      if (!data) throw new Error(extractDetail(error) ?? "讀取健康度失敗");
      return data;
    },
    retry: false,
    // 有備份進行中時每 3 秒重取，讓狀態即時更新。
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });

  const runsQuery = useQuery({
    queryKey: ["backup-runs"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/backup/runs");
      if (!data) throw new Error(extractDetail(error) ?? "讀取備份紀錄失敗");
      return data;
    },
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "RUNNING") ? 3000 : false,
  });

  const restoresQuery = useQuery({
    queryKey: ["backup-restores"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/backup/restores");
      if (!data) throw new Error(extractDetail(error) ?? "讀取還原紀錄失敗");
      return data;
    },
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "RUNNING") ? 3000 : false,
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["backup-health"] });
    void queryClient.invalidateQueries({ queryKey: ["backup-runs"] });
    void queryClient.invalidateQueries({ queryKey: ["backup-restores"] });
  }

  if (healthQuery.isPending) return <p>載入中...</p>;
  if (healthQuery.error instanceof ForbiddenError) {
    return (
      <section>
        <h1 className="page-title">備份</h1>
        <p className="hint">需管理者權限</p>
      </section>
    );
  }
  if (healthQuery.isError) {
    return (
      <p role="alert" className="form-error">
        {healthQuery.error.message}
      </p>
    );
  }

  return (
    <section>
      <h1 className="page-title">備份</h1>
      <div className="card-stack">
        <HealthCard health={healthQuery.data} onTriggered={refresh} />
        <BackupSettingsCard health={healthQuery.data} onSaved={refresh} />
        <RunsCard runs={runsQuery.data ?? []} />
        <RestoreCard
          backups={runsQuery.data ?? []}
          restores={restoresQuery.data ?? []}
          retention={healthQuery.data.retention}
          onTriggered={refresh}
        />
      </div>
    </section>
  );
}
