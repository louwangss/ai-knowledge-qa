import { readFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const currentDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(currentDir, "..", "..");
const visualOnly = process.argv.includes("--visual-only");
const outputArgument = process.argv.slice(2).find((argument) => !argument.startsWith("--"));
const outputPath = resolve(outputArgument || resolve(projectRoot, "data", "chat-web-smoke.png"));

function readEnv() {
  const values = {};
  const content = readFileSync(resolve(projectRoot, ".env"), "utf8");
  for (const line of content.split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator > 0) values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  if (!values.APP_ACCESS_TOKEN) throw new Error("APP_ACCESS_TOKEN 未配置");
  return values;
}

const env = readEnv();
const createdSessionIds = [];
const consoleErrors = [];
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (message) => {
  if (message.type() === "error" || message.type() === "warning") consoleErrors.push(`${message.type()}: ${message.text()}`);
});
page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

async function createSession() {
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/sessions") && response.request().method() === "POST",
  );
  await page.getByLabel("会话导航").getByRole("button", { name: "新建会话" }).click();
  const response = await responsePromise;
  if (!response.ok()) throw new Error(`新建会话失败：${response.status()}`);
  const session = await response.json();
  createdSessionIds.push(session.id);
  await page.getByText("从你的资料里找到答案").waitFor();
}

async function ask(question, mode) {
  if (mode === "deep") await page.getByRole("button", { name: "深度研究", exact: true }).click();
  await page.getByRole("textbox", { name: "输入问题" }).fill(question);
  await page.getByRole("button", { name: "发送问题" }).click();
  const answer = page.locator(".chat-message.assistant").last();
  await answer.waitFor({ timeout: 120_000 });
  await page.waitForFunction(() => {
    const lastAnswer = document.querySelector(".chat-message.assistant:last-of-type");
    return Boolean(lastAnswer?.querySelector(".message-content"))
      && !lastAnswer?.querySelector(".message-stage")
      && !document.querySelector(".send-button.stop");
  }, undefined, { timeout: 120_000 });
  return (await answer.locator(".message-content").innerText()).trim();
}

async function captureScreenshots() {
  mkdirSync(dirname(outputPath), { recursive: true });
  await page.screenshot({ path: outputPath, fullPage: true });
  const mobileOutputPath = outputPath.replace(/\.png$/i, "-mobile.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开会话列表" }).click();
  await page.waitForFunction(() => {
    const sidebar = document.querySelector(".sidebar.is-open");
    if (!sidebar) return false;
    const transform = getComputedStyle(sidebar).transform;
    return transform === "none" || transform === "matrix(1, 0, 0, 1, 0, 0)";
  });
  await page.screenshot({ path: mobileOutputPath, fullPage: true });
  return mobileOutputPath;
}

try {
  await page.goto("http://127.0.0.1:5173/app/?view=chat", { waitUntil: "networkidle" });
  const tokenInput = page.getByLabel("访问令牌");
  if (await tokenInput.isVisible()) {
    await tokenInput.fill(env.APP_ACCESS_TOKEN);
    await page.getByRole("button", { name: "进入笔记" }).click();
  }
  await page.getByLabel("会话导航").getByRole("button", { name: "新建会话" }).waitFor();

  let normalAnswer = "";
  let deepAnswer = "";
  if (visualOnly) {
    const sessionItems = page.getByLabel("会话列表").locator(".note-list-item");
    for (let index = 0; index < await sessionItems.count(); index += 1) {
      await sessionItems.nth(index).click();
      await page.waitForTimeout(100);
      if (await page.locator(".message-content").count()) break;
    }
    await page.locator(".message-content").last().waitFor();
  } else {
    await createSession();
    normalAnswer = await ask("请用一句中文说明这个系统的用途。", "normal");
    await createSession();
    deepAnswer = await ask("请简要分析 RAG 系统回答问题的主要步骤。", "deep");
  }

  const browserStorage = await page.evaluate(() => ({
    localStorageKeys: Object.keys(localStorage),
    sessionStorageKeys: Object.keys(sessionStorage),
    urlContainsCredential: /token|bootstrap/i.test(location.href),
  }));
  const mobileOutputPath = await captureScreenshots();

  console.log(JSON.stringify({
    normalAnswerChars: normalAnswer.length,
    deepAnswerChars: deepAnswer.length,
    consoleErrors,
    browserStorage,
    screenshot: outputPath,
    mobileScreenshot: mobileOutputPath,
  }));
} finally {
  await browser.close();
  for (const sessionId of createdSessionIds) {
    await fetch(`http://127.0.0.1:8000/api/v1/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(env.APP_USER_ID || "default-user")}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${env.APP_ACCESS_TOKEN}` },
      signal: AbortSignal.timeout(10_000),
    }).catch(() => undefined);
  }
}
