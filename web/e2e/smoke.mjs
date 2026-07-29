import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const currentDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(currentDir, "..", "..");
const outputPath = resolve(process.argv[2] || resolve(projectRoot, "data", "notes-web-smoke.png"));

function readAccessToken() {
  const content = readFileSync(resolve(projectRoot, ".env"), "utf8");
  const line = content.split(/\r?\n/).find((item) => item.startsWith("APP_ACCESS_TOKEN="));
  if (!line) throw new Error("APP_ACCESS_TOKEN 未配置");
  return line.slice(line.indexOf("=") + 1).trim();
}

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

try {
  await page.goto("http://127.0.0.1:5173/app/", { waitUntil: "networkidle" });
  const tokenInput = page.getByLabel("访问令牌");
  if (await tokenInput.isVisible()) {
    await tokenInput.fill(readAccessToken());
    await page.getByRole("button", { name: "进入工作区" }).click();
  }
  await page.locator(".editor-shell").waitFor();
  await page.locator(".note-list-item").first().waitFor();
  await page.locator(".content-input").waitFor();

  const listItems = page.locator(".note-list-item");
  const count = await listItems.count();
  let coldSwitchMs = null;
  let cachedSwitchMs = null;
  if (count > 1) {
    const secondLabel = await listItems.nth(1).getAttribute("aria-label");
    const coldStarted = performance.now();
    await listItems.nth(1).click();
    await page.waitForFunction(
      (label) => document.querySelector(".note-list-item[aria-current='page']")?.getAttribute("aria-label") === label
        && Boolean(document.querySelector(".content-input")),
      secondLabel,
    );
    coldSwitchMs = Math.round(performance.now() - coldStarted);

    const firstLabel = await listItems.nth(0).getAttribute("aria-label");
    const cachedStarted = performance.now();
    await listItems.nth(0).click();
    await page.waitForFunction(
      (label) => document.querySelector(".note-list-item[aria-current='page']")?.getAttribute("aria-label") === label
        && Boolean(document.querySelector(".content-input")),
      firstLabel,
    );
    cachedSwitchMs = Math.round(performance.now() - cachedStarted);
  }

  const browserStorage = await page.evaluate(() => ({
    localStorageKeys: Object.keys(localStorage),
    sessionStorageKeys: Object.keys(sessionStorage),
    urlContainsToken: location.href.includes("token") || location.href.includes("bootstrap"),
  }));
  mkdirSync(dirname(outputPath), { recursive: true });
  await page.screenshot({ path: outputPath, fullPage: true });
  const mobileOutputPath = outputPath.replace(/\.png$/i, "-mobile.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开笔记列表" }).click();
  await page.waitForFunction(() => {
    const sidebar = document.querySelector(".sidebar");
    if (!sidebar) return false;
    const transform = getComputedStyle(sidebar).transform;
    return transform === "none" || transform === "matrix(1, 0, 0, 1, 0, 0)";
  });
  await page.screenshot({ path: mobileOutputPath, fullPage: true });

  console.log(JSON.stringify({
    noteCount: count,
    coldSwitchMs,
    cachedSwitchMs,
    consoleErrors,
    browserStorage,
    screenshot: outputPath,
    mobileScreenshot: mobileOutputPath,
  }));
} finally {
  await browser.close();
}
