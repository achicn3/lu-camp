// 手冊內容用的小工具：圖說、步驟、提示區塊、表格。
export function fig(id, caption, opts = {}) {
  const { width } = opts;
  return `<figure class="shot"${width ? ` style="max-width:${width}px"` : ""}>
  <img data-img="${id}" alt="${escapeAttr(caption)}" loading="lazy" />
  <figcaption>${caption}</figcaption>
</figure>`;
}

export function figs(...items) {
  return `<div class="shot-row">${items.join("\n")}</div>`;
}

export function steps(list) {
  return `<ol class="steps">${list.map((s) => `<li>${s}</li>`).join("")}</ol>`;
}

export function box(kind, title, html) {
  return `<div class="box box-${kind}"><p class="box-title">${title}</p>${html}</div>`;
}

export function table(headers, rows) {
  return `<div class="table-wrap"><table>
<thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>
</table></div>`;
}

export function flow(nodes) {
  return `<div class="flow">${nodes
    .map((n, i) => `<span class="flow-node">${n}</span>${i < nodes.length - 1 ? '<span class="flow-arrow" aria-hidden="true">→</span>' : ""}`)
    .join("")}</div>`;
}

export function meta(rows) {
  return `<dl class="meta">${rows
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`)
    .join("")}</dl>`;
}

export function roleTag(role) {
  const cls = role === "管理員" ? "tag-admin" : role === "需要特定權限" ? "tag-perm" : "tag-all";
  return `<span class="tag ${cls}">${role}</span>`;
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function kbd(s) {
  return `<kbd>${s}</kbd>`;
}

/** 欄位表：名稱 / 必填 / 格式限制 / 說明 */
export function fields(rows) {
  return table(
    ["欄位", "必填", "格式 / 限制", "說明"],
    rows.map(([name, required, format, desc]) => [
      `<b>${name}</b>`,
      required ? '<span class="req">必填</span>' : '<span class="opt">選填</span>',
      format,
      desc,
    ]),
  );
}
