import { readFileSync, writeFileSync, unlinkSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const currentDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(currentDir, "..", "..");
const outputPath = resolve(process.argv[2] || resolve(projectRoot, "data", "documents-web-smoke.png"));
const uploadPath = resolve(tmpdir(), `knowledge-documents-smoke-${Date.now()}.md`);
const consoleErrors = [];
let createdDocumentId = null;

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
writeFileSync(uploadPath, "# React 文档管理验收\n\n这是一份自动生成且会在验收后删除的临时资料。\n", "utf8");

const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (message) => {
  if (message.type() === "error" || message.type() === "warning") consoleErrors.push(`${message.type()}: ${message.text()}`);
});
page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));

try {
  await page.goto("http://127.0.0.1:5173/app/?view=documents", { waitUntil: "networkidle" });
  const tokenInput = page.getByLabel("访问令牌");
  if (await tokenInput.isVisible()) {
    await tokenInput.fill(env.APP_ACCESS_TOKEN);
    await page.getByRole("button", { name: "进入工作区" }).click();
  }
  await page.getByRole("main").waitFor();
  await page.getByRole("button", { name: "文档", exact: true }).waitFor();
  await page.locator(".document-library[aria-busy='false']").waitFor();

  const uploadResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/documents") && response.request().method() === "POST",
    { timeout: 120_000 },
  );
  await page.getByLabel("选择文档").setInputFiles(uploadPath);
  const uploadResponse = await uploadResponsePromise;
  if (!uploadResponse.ok()) throw new Error(`上传失败：${uploadResponse.status()}`);
  const createdDocument = await uploadResponse.json();
  createdDocumentId = createdDocument.id;
  const uploadedRow = page.getByRole("listitem").filter({ hasText: createdDocument.filename });
  try {
    await uploadedRow.waitFor({ timeout: 10_000 });
  } catch (cause) {
    const visibleText = await page.locator("body").innerText();
    throw new Error(`上传成功但列表未更新：filename=${createdDocument.filename}; page=${visibleText.slice(0, 800)}`, { cause });
  }

  const viewportChecks = [];
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: width === 320 ? 720 : 900 });
    const layout = await page.evaluate(() => ({
      width: window.innerWidth,
      bodyWidth: document.body.scrollWidth,
      hasMain: Boolean(document.querySelector("main")),
      hasHeading: Boolean(document.querySelector("h1")),
    }));
    viewportChecks.push({ ...layout, overflow: layout.bodyWidth > layout.width });
  }

  await page.setViewportSize({ width: 1440, height: 1000 });
  mkdirSync(dirname(outputPath), { recursive: true });
  await page.screenshot({ path: outputPath, fullPage: true });
  const mobileOutputPath = outputPath.replace(/\.png$/i, "-mobile.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开文档导航" }).click();
  await page.getByRole("complementary", { name: "文档导航" }).waitFor();
  await page.waitForFunction(() => {
    const sidebar = document.querySelector(".sidebar.is-open");
    if (!sidebar) return false;
    const transform = getComputedStyle(sidebar).transform;
    return transform === "none" || transform === "matrix(1, 0, 0, 1, 0, 0)";
  });
  await page.screenshot({ path: mobileOutputPath, fullPage: true });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: `删除 ${createdDocument.filename}` }).click();
  const deleteResponsePromise = page.waitForResponse((response) =>
    response.url().includes(`/api/v1/documents/${createdDocumentId}`) && response.request().method() === "DELETE",
  );
  await page.getByRole("button", { name: "确认删除" }).click();
  const deleteResponse = await deleteResponsePromise;
  if (!deleteResponse.ok()) throw new Error(`删除失败：${deleteResponse.status()}`);
  createdDocumentId = null;

  const browserStorage = await page.evaluate(() => ({
    localStorageKeys: Object.keys(localStorage),
    sessionStorageKeys: Object.keys(sessionStorage),
    urlContainsCredential: /token|bootstrap/i.test(location.href),
  }));
  console.log(JSON.stringify({
    consoleErrors,
    viewportChecks,
    browserStorage,
    screenshot: outputPath,
    mobileScreenshot: mobileOutputPath,
  }));
} finally {
  await browser.close();
  unlinkSync(uploadPath);
  if (createdDocumentId) {
    await fetch(`http://127.0.0.1:8000/api/v1/documents/${encodeURIComponent(createdDocumentId)}?user_id=${encodeURIComponent(env.APP_USER_ID || "default-user")}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${env.APP_ACCESS_TOKEN}` },
      signal: AbortSignal.timeout(30_000),
    }).catch(() => undefined);
  }
}
