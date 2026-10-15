// 桌號 QR（店主 2026-10-15）：把客人掃碼的網址編成 QR 方格，畫成 SVG 列印。
import qrcode from "qrcode-generator";

/** QR 方格（true＝黑格）。容錯 M：貼紙有點刮傷或反光還掃得到，網址長度也放得下。 */
export function qrMatrix(text: string): boolean[][] {
  const qr = qrcode(0, "M");
  qr.addData(text, "Byte");
  qr.make();
  const size = qr.getModuleCount();
  return Array.from({ length: size }, (_, row) =>
    Array.from({ length: size }, (_, col) => qr.isDark(row, col)),
  );
}

/** 黑格的 SVG 路徑，座標以格為單位（viewBox 設成方格大小即可縮放）。 */
export function qrPath(matrix: boolean[][]): string {
  return matrix
    .flatMap((row, r) => row.map((dark, c) => (dark ? `M${c} ${r}h1v1h-1z` : "")))
    .join("");
}
