// Playwright MCP 서버 실행기(.mcp.json이 부름). 클라우드 작업 환경엔 미리 깔린 Chromium(/opt/pw-browsers/chromium)이 있어 그것을 쓰고,
// 다른 PC에선 Playwright가 받아 둔 브라우저를 씀(없으면 그 PC에서 `npx playwright install chromium` 한 번).
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const env = { ...process.env };
const preinstalled = "/opt/pw-browsers/chromium";
if (!env.PLAYWRIGHT_MCP_EXECUTABLE_PATH && existsSync(preinstalled)) env.PLAYWRIGHT_MCP_EXECUTABLE_PATH = preinstalled;
const args = ["-y", "@playwright/mcp@0.0.86", "--headless", "--isolated"];
if (process.platform === "linux") args.push("--no-sandbox");
const child = spawn(process.platform === "win32" ? "npx.cmd" : "npx", args, { stdio: "inherit", env, shell: process.platform === "win32" });
child.on("exit", (code) => process.exit(code ?? 1));
