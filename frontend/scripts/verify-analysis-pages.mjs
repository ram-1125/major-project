const sizes = [
  [1920, 1080],
  [1366, 768],
  [1024, 768],
  [768, 900],
  [390, 844],
];

const target = (await fetch("http://127.0.0.1:9222/json").then((response) => response.json()))
  .find((item) => item.type === "page");
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
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function evaluate(expression) {
  const response = await command("Runtime.evaluate", { expression, returnByValue: true });
  return response.result.value;
}
async function waitFor(expression, description, timeout = 60_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${description}.`);
}

await command("Runtime.enable");
await command("Page.enable");
const results = [];

for (const [width, height] of sizes) {
  await command("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width <= 600,
  });
  for (const route of ["root-cause-analysis", "system-health"]) {
    await command("Page.navigate", { url: `http://localhost:5173/#/${route}` });
    await delay(750);
    const marker = route === "root-cause-analysis" ? ".rca-command" : ".health-command";
    await waitFor(
      `Boolean(document.querySelector('${marker}')) && Boolean(document.querySelector('.refresh-button:not(:disabled)'))`,
      `${route} data`,
    );

    if (route === "root-cause-analysis") {
      await evaluate(`(() => {
        const select = document.querySelector('.analysis-filter-panel select');
        select.value = 'all';
        select.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      })()`);
      await waitFor("Boolean(document.querySelector('.investigation-card'))", "an RCA investigation");
      await evaluate(`(() => {
        const button = document.querySelector('.analysis-toggle');
        if (button.getAttribute('aria-expanded') !== 'true') button.click();
        return true;
      })()`);
      await waitFor("Boolean(document.querySelector('.investigation-workspace'))", "on-demand RCA detail");
    } else {
      await evaluate(`(() => {
        const button = [...document.querySelectorAll('.health-trend-section .range-control button')]
          .find((item) => item.textContent?.trim() === 'All data');
        button?.click();
        return Boolean(button);
      })()`);
      await waitFor("document.querySelectorAll('.health-history-section tbody tr').length > 0", "health history");
    }

    const diagnostics = await evaluate(`(() => {
      const root = document.documentElement;
      const appContent = document.querySelector('.app-content');
      const routeRoot = document.querySelector(${JSON.stringify(marker)});
      return {
        route: ${JSON.stringify(route)},
        width: ${width},
        height: ${height},
        rootOverflow: root.scrollWidth > root.clientWidth + 1,
        documentScroller: document.scrollingElement?.tagName ?? null,
        nestedMainScroll: ['auto', 'scroll'].includes(getComputedStyle(appContent).overflowY),
        routePresent: Boolean(routeRoot),
        stageLabelVisible: routeRoot?.innerText.includes('Stage 4') ?? false,
        errorPanel: Boolean(document.querySelector('.state-panel--error')),
        detailLoaded: ${route === "root-cause-analysis" ? "Boolean(document.querySelector('.investigation-workspace'))" : "Boolean(document.querySelector('.health-command-hero'))"},
        contributorMeter: ${route === "root-cause-analysis" ? "Boolean(document.querySelector('.contributor-bar[role=meter]'))" : "Boolean(document.querySelector('.health-component-bars [role=meter]'))"},
        linkedEvidence: Boolean(document.querySelector('.analysis-related-links a')),
      };
    })()`);
    results.push(diagnostics);
  }
}

// Exercise the real expanded chart focus lifecycle once at laptop width.
await command("Emulation.setDeviceMetricsOverride", { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
await command("Page.navigate", { url: "http://localhost:5173/#/system-health" });
await delay(750);
await waitFor("Boolean(document.querySelector('.health-trend-section .chart-expand'))", "health chart expand control");
await evaluate("document.querySelector('.health-trend-section .chart-expand').click(); true");
await waitFor("Boolean(document.querySelector('[role=dialog]'))", "expanded health chart");
const dialogFocused = await evaluate("document.querySelector('[role=dialog]').contains(document.activeElement)");
await evaluate(`(() => {
  const dialog = document.querySelector('[role=dialog]');
  dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  return true;
})()`);
await waitFor("!document.querySelector('[role=dialog]')", "Escape to close the chart");
const focusReturned = await evaluate("document.activeElement === document.querySelector('.health-trend-section .chart-expand')");

socket.close();
const failures = results.filter((item) =>
  item.rootOverflow || item.nestedMainScroll || !item.routePresent || item.stageLabelVisible
  || item.errorPanel || !item.detailLoaded || !item.contributorMeter || !item.linkedEvidence,
);
console.log(JSON.stringify({ results, dialogFocused, focusReturned, browserErrors }, null, 2));
if (failures.length || !dialogFocused || !focusReturned || browserErrors.length) {
  throw new Error("Root-Cause Analysis or System Health browser verification failed.");
}
