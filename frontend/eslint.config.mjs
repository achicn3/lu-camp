import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // 上線前資安（docs/44 §8.4）：畫面上的文字一律交給 React 當純文字顯示，不准把字串當 HTML 塞進頁面。
  {
    rules: {
      "react/no-danger": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "AssignmentExpression > MemberExpression[property.name=/^(innerHTML|outerHTML)$/]",
          message: "不要用 innerHTML／outerHTML 塞字串，改用 React 或 textContent（XSS）。",
        },
        {
          selector: "CallExpression[callee.property.name='insertAdjacentHTML']",
          message: "不要用 insertAdjacentHTML，改用 React 或 textContent（XSS）。",
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
