import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const [
  alertTitle = "Memory and page-file pressure",
  outputPath,
  widthText = "1366",
  heightText = "768",
] = process.argv.slice(2);
const width = Number(widthText);
const height = Number(heightText);
if (!outputPath) {
  throw new Error("Usage: node scripts/verify-alert-details.mjs ALERT_TITLE OUTPUT_PNG [WIDTH] [HEIGHT]");
}

const deadline = Date.now() + 90_000;
let target;
while (Date.now() < deadline) {
  try {
    const targets = await fetch("http://127.0.0.1:9222/json").then((response) => response.json());
    target = targets.find((item) => item.type === "page");
    if (target) break;
  } catch {
    // Edge may still be opening its dedicated local profile.
  }
  await new Promise((resolve) => setTimeout(resolve, 250));
}
if (!target) throw new Error("No local Edge page target is available.");

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});
let identifier = 0;
const pending = new Map();
const browserErrors = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (message.method === "Runtime.exceptionThrown") {
    browserErrors.push(message.params?.exceptionDetails?.text ?? "Browser exception");
  }
  if (message.method === "Runtime.consoleAPICalled" && message.params?.type === "error") {
    browserErrors.push("Console error");
  }
  if (!message.id || !pending.has(message.id)) return;
  const callback = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) callback.reject(new Error(message.error.message));
  else callback.resolve(message.result);
});
function command(method, params = {}) {
  const id = ++identifier;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const response = await command("Runtime.evaluate", { expression, returnByValue: true });
  return response.result.value;
}
async function waitFor(expression, message) {
  const waitDeadline = Date.now() + 90_000;
  while (Date.now() < waitDeadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(message);
}

await command("Page.enable");
await command("Runtime.enable");
await command("Emulation.setDeviceMetricsOverride", {
  width,
  height,
  deviceScaleFactor: 1,
  mobile: width <= 600,
});
await command("Page.navigate", { url: "http://localhost:5173/#/predictive-alerts" });
await waitFor(
  "Boolean(document.querySelector('.alerts-section:not([hidden])')) && !Boolean(document.querySelector('.state-panel--error'))",
  "Predictive Alerts did not finish loading.",
);

await evaluate(`(() => {
  const label = [...document.querySelectorAll('.alert-filters label')]
    .find((item) => item.querySelector('span')?.textContent?.trim() === 'Time range');
  const select = label?.querySelector('select');
  if (!select) return false;
  select.value = 'all';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})()`);
await waitFor(
  `(() => [...document.querySelectorAll('.alert-history tbody tr')].some((row) => row.textContent?.includes(${JSON.stringify(alertTitle)}) && row.querySelector('button.alert-table-detail-button')))()`,
  `No resolved row with a View details control was found for ${alertTitle}.`,
);

const beforeClick = await evaluate(`(() => {
  const row = [...document.querySelectorAll('.alert-history tbody tr')]
    .find((item) => item.textContent?.includes(${JSON.stringify(alertTitle)}) && item.querySelector('button.alert-table-detail-button'));
  row?.scrollIntoView({ block: 'center' });
  const button = row?.querySelector('button.alert-table-detail-button');
  const rect = button?.getBoundingClientRect();
  return rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, scrollY: window.scrollY } : null;
})()`);
if (!beforeClick) throw new Error("The resolved alert control has no clickable bounds.");
if (width <= 600) {
  // The historical table intentionally owns horizontal overflow on a phone.
  // Activate its real button after locating it rather than dispatching outside
  // the emulated viewport before the inner table has been horizontally panned.
  await evaluate(`(() => {
    const row = [...document.querySelectorAll('.alert-history tbody tr')]
      .find((item) => item.textContent?.includes(${JSON.stringify(alertTitle)}) && item.querySelector('button.alert-table-detail-button'));
    row?.querySelector('button.alert-table-detail-button')?.click();
    return true;
  })()`);
} else {
  await command("Input.dispatchMouseEvent", { type: "mousePressed", x: beforeClick.x, y: beforeClick.y, button: "left", clickCount: 1 });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", x: beforeClick.x, y: beforeClick.y, button: "left", clickCount: 1 });
}

await waitFor(
  `(() => {
    const row = [...document.querySelectorAll('.alert-history tbody tr')]
      .find((item) => item.textContent?.includes(${JSON.stringify(alertTitle)}) && item.querySelector('button[aria-expanded="true"]'));
    const detail = row?.nextElementSibling?.querySelector('.alert-detail-region');
    return Boolean(detail && detail.textContent?.includes('Why the alert was generated'));
  })()`,
  `Full details did not open for ${alertTitle}.`,
);

const diagnostics = await evaluate(`(() => {
  const root = document.documentElement;
  const footer = document.querySelector('#main-content > footer');
  const row = [...document.querySelectorAll('.alert-history tbody tr')]
    .find((item) => item.textContent?.includes(${JSON.stringify(alertTitle)}) && item.querySelector('button[aria-expanded="true"]'));
  const detail = row?.nextElementSibling?.querySelector('.alert-detail-region');
  const text = detail?.textContent ?? '';
  return {
    alertId: detail?.id?.replace('alert-details-', '') ?? null,
    expanded: row?.querySelector('button')?.getAttribute('aria-expanded') === 'true',
    controlText: row?.querySelector('button')?.textContent?.trim() ?? null,
    hasWhy: text.includes('Why the alert was generated'),
    hasConfidence: text.includes('Alert confidence basis'),
    hasValidation: text.includes('Not yet validated') || text.includes('Method-level validation'),
    hasLifecycle: text.includes('Actual lifecycle and notification history'),
    hasRca: text.includes('Related Root-Cause Analysis evidence'),
    documentHeight: root.scrollHeight,
    viewportHeight: root.clientHeight,
    documentWidth: root.scrollWidth,
    viewportWidth: root.clientWidth,
    footerBottom: footer?.getBoundingClientRect().bottom ?? null,
    footerInDocument: Boolean(footer),
    trailingDocumentSpace: footer ? Math.max(0, root.scrollHeight - (footer.getBoundingClientRect().bottom + window.scrollY)) : null,
  };
})()`);
if (!diagnostics.expanded || !diagnostics.hasWhy || !diagnostics.hasConfidence || !diagnostics.hasValidation || !diagnostics.hasLifecycle || !diagnostics.hasRca) {
  throw new Error("The expanded alert is missing required stored-detail sections.");
}
if (diagnostics.documentWidth > diagnostics.viewportWidth + 1) {
  throw new Error("Predictive Alerts has root-level horizontal overflow.");
}
if (!diagnostics.footerInDocument || diagnostics.trailingDocumentSpace > 1) {
  throw new Error("The footer is not the final document content boundary.");
}

const layout = await command("Page.getLayoutMetrics");
const contentSize = layout.cssContentSize;
const screenshot = await command("Page.captureScreenshot", {
  format: "png",
  fromSurface: true,
  captureBeyondViewport: true,
  clip: { x: 0, y: 0, width: contentSize.width, height: contentSize.height, scale: 1 },
});
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, Buffer.from(screenshot.data, "base64"));
socket.close();

console.log(JSON.stringify({
  alertTitle,
  capturedWidth: contentSize.width,
  capturedHeight: contentSize.height,
  ...diagnostics,
  browserErrors,
}, null, 2));
if (browserErrors.length > 0) throw new Error("Browser console errors were recorded.");
