// 桌號 QR（店主 2026-10-15 B.1）：畫出來的方格要真的掃得回客人的點餐網址。
import jsQR from "jsqr";
import { describe, expect, it } from "vitest";

import { qrMatrix, qrPath } from "@/features/menu/tableQr";

/** 把方格畫成黑白點陣（每格 4px、四周留 4 格白邊）給 jsqr 解。 */
function decode(matrix: boolean[][]): string | null {
  const scale = 4, quiet = 4, size = (matrix.length + quiet * 2) * scale;
  const data = new Uint8ClampedArray(size * size * 4).fill(255);
  matrix.forEach((row, r) => row.forEach((dark, c) => {
    if (!dark) return;
    for (let y = 0; y < scale; y++) for (let x = 0; x < scale; x++) {
      const i = (((r + quiet) * scale + y) * size + (c + quiet) * scale + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = 0;
    }
  }));
  return jsQR(data, size, size)?.data ?? null;
}

describe("桌號 QR", () => {
  it("掃得回完整的點餐網址", () => {
    const url = "https://order.example.com/t/tA3kq9ZxWm2pLr7v_-Ab";
    const matrix = qrMatrix(url);
    expect(matrix.length).toBe(matrix[0]!.length);
    expect(decode(matrix)).toBe(url);
  });
  it("SVG 路徑：每個黑格一個 1×1 方塊，座標以格為單位", () => {
    expect(qrPath([[true, false], [false, true]])).toBe("M0 0h1v1h-1zM1 1h1v1h-1z");
  });
});
