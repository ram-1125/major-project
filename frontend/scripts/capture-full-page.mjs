import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const [route = "live-monitoring", widthText = "1366", heightText = "768", outputPath, captureMode = "summary"] = process.argv.slice(2);
const width = Number(widthText);
const height = Number(heightText);
if (!outputPath || !Number.isFinite(width) || !Number.isFinite(height)) {
  throw new Error("Usage: node scripts/capture-full-page.mjs ROUTE WIDTH HEIGHT OUTPUT");
}

const deadline = Date.now() + 90_000;
let target;
while (Date.now() < deadline) {
  try {
    const targets = await fetch("http://127.0.0.1:9222/json").then((response) => response.json());
    target = targets.find((item) => item.type === "page");
    if (target) break;
  } catch {
    // The dedicated local Edge process may still be opening.
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

await command("Page.enable");
await command("Runtime.enable");
await command("Emulation.setDeviceMetricsOverride", {
  width,
  height,
  deviceScaleFactor: 1,
  mobile: width <= 600,
});
await command("Page.navigate", { url: `http://localhost:5173/#/${route}` });
// Let the route effect enter its loading state before testing readiness. Without
// this small guard, a same-document hash navigation can expose the previous
// route's enabled refresh button for one animation frame.
await new Promise((resolve) => setTimeout(resolve, 750));

const routeMarker = {
  "overview": ".overview-command",
  "live-monitoring": ".live-command",
  "predictive-alerts": ".alerts-section:not([hidden])",
  "root-cause-analysis": ".rca-command",
  "system-health": ".health-command",
  "pc-quality-check": ".quality-section:not([hidden])",
  "research-validation": ".validation-section:not([hidden])",
  "settings": ".settings-page:not([hidden])",
}[route] ?? "#main-content";
let ready = false;
while (Date.now() < deadline) {
  const response = await command("Runtime.evaluate", {
    expression: `Boolean(document.querySelector(${JSON.stringify(routeMarker)})) && Boolean(document.querySelector('.refresh-button:not(:disabled)')) && !Boolean(document.querySelector('.state-panel--error'))`,
    returnByValue: true,
  });
  if (response.result.value) {
    ready = true;
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!ready) throw new Error(`${route} did not finish loading local data.`);
if (route === "root-cause-analysis") {
  const selectedAllHistory = await command("Runtime.evaluate", {
    expression: `(() => {
      const select = document.querySelector('.analysis-filter-panel select');
      if (!select || ![...select.options].some((option) => option.value === 'all')) return false;
      select.value = 'all';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    })()`,
    returnByValue: true,
  });
  if (selectedAllHistory.result.value) {
    const historyDeadline = Date.now() + 30_000;
    while (Date.now() < historyDeadline) {
      const result = await command("Runtime.evaluate", {
        expression: `Boolean(document.querySelector('.investigation-card'))`,
        returnByValue: true,
      });
      if (result.result.value) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}
if (route === "system-health") {
  const selectedAllHistory = await command("Runtime.evaluate", {
    expression: `(() => {
      const button = [...document.querySelectorAll('.health-trend-section .range-control button')]
        .find((item) => item.textContent?.trim() === 'All data');
      if (!button) return false;
      button.click();
      return true;
    })()`,
    returnByValue: true,
  });
  if (selectedAllHistory.result.value) {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
}
if (route === "root-cause-analysis") {
  const requestedExpandedState = captureMode === "expanded";
  const changedExpansion = await command("Runtime.evaluate", {
    expression: `(() => {
      const button = document.querySelector('.analysis-toggle');
      if (!button) return false;
      const expanded = button.getAttribute('aria-expanded') === 'true';
      if (expanded !== ${captureMode === "expanded"}) button.click();
      return true;
    })()`,
    returnByValue: true,
  });
  if (changedExpansion.result.value) {
    const detailDeadline = Date.now() + 30_000;
    while (Date.now() < detailDeadline) {
      const result = await command("Runtime.evaluate", {
        expression: requestedExpandedState
          ? `Boolean(document.querySelector('.investigation-workspace, .analysis-detail-state--error'))`
          : `!Boolean(document.querySelector('.investigation-detail'))`,
        returnByValue: true,
      });
      if (result.result.value) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
}
// The route-level refresh button is enabled once the coordinated request has
// settled. Give React one final paint to render on-demand summaries before a
// full-page capture; this prevents an earlier route's transient DOM from being
// photographed after a same-document hash navigation.
await new Promise((resolve) => setTimeout(resolve, 2_000));
await command("Runtime.evaluate", { expression: "window.scrollTo(0, 0)" });

const diagnostics = await command("Runtime.evaluate", {
  expression: `(() => {
    const root = document.documentElement;
    const content = document.querySelector('.app-content');
    const footer = document.querySelector('#main-content > footer');
    const style = content ? getComputedStyle(content) : null;
    return {
      documentScroller: document.scrollingElement?.tagName ?? null,
      documentHeight: root.scrollHeight,
      viewportHeight: root.clientHeight,
      documentWidth: root.scrollWidth,
      viewportWidth: root.clientWidth,
      appContentHeight: content?.clientHeight ?? null,
      appContentScrollHeight: content?.scrollHeight ?? null,
      appContentOverflowY: style?.overflowY ?? null,
      liveCommandCount: document.querySelectorAll('.live-command').length,
      visibleSectionCount: [...document.querySelectorAll('#main-content > section')]
        .filter((section) => !section.hidden).length,
      visibleSections: [...document.querySelectorAll('#main-content > section')]
        .filter((section) => !section.hidden)
        .map((section) => ({
          className: section.className,
          top: section.getBoundingClientRect().top,
          bottom: section.getBoundingClientRect().bottom,
          height: section.getBoundingClientRect().height,
        })),
      footerBottom: footer?.getBoundingClientRect().bottom ?? null,
      bottomMarker: footer?.textContent?.includes('Local-only') ?? false,
    };
  })()`,
  returnByValue: true,
});
const observed = diagnostics.result.value;
if (observed.appContentOverflowY === "auto" || observed.appContentOverflowY === "scroll") {
  throw new Error("Main content still owns a nested vertical scrollbar.");
}
if (!observed.bottomMarker || observed.footerBottom > observed.documentHeight + 1) {
  throw new Error("The bottom local-only footer is outside the captured document.");
}
if (observed.documentWidth > observed.viewportWidth + 1) {
  throw new Error("The route has root-level horizontal overflow.");
}
if (observed.documentHeight <= observed.viewportHeight) {
  throw new Error("The full-page route did not expand beyond the viewport.");
}
const metrics = await command("Page.getLayoutMetrics");
const contentSize = metrics.cssContentSize;
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
  route,
  width,
  height,
  captureMode,
  capturedWidth: contentSize.width,
  capturedHeight: contentSize.height,
  ...diagnostics.result.value,
  browserErrors,
}, null, 2));
