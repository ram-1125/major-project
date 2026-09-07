const routes = [
  ["overview", "Overview"],
  ["live-monitoring", "Live Monitoring"],
  ["predictive-alerts", "Predictive Alerts"],
  ["root-cause-analysis", "Root-Cause Analysis"],
  ["system-health", "System Health"],
  ["pc-quality-check", "PC Quality Check"],
  ["research-validation", "Research & Validation"],
  ["settings", "Settings"],
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
await command("Runtime.enable");
await command("Page.enable");
await command("Emulation.setDeviceMetricsOverride", {
  width: 1366,
  height: 768,
  deviceScaleFactor: 1,
  mobile: false,
});

const results = [];
for (const [route, label] of routes) {
  await command("Page.navigate", { url: `http://localhost:5173/#/${route}` });
  await new Promise((resolve) => setTimeout(resolve, 750));
  const deadline = Date.now() + 60_000;
  let result;
  while (Date.now() < deadline) {
    const response = await command("Runtime.evaluate", {
      expression: `(() => {
        const label = document.querySelector('.topbar__page strong')?.textContent?.trim();
        const root = document.documentElement;
        return {
          label,
          expected: ${JSON.stringify(label)},
          hasMain: Boolean(document.querySelector('#main-content')),
          refreshReady: Boolean(document.querySelector('.refresh-button:not(:disabled)')),
          overflow: root.scrollWidth > root.clientWidth,
          errorPanel: Boolean(document.querySelector('.state-panel--error')),
        };
      })()`,
      returnByValue: true,
    });
    result = response.result.value;
    if (result.label === label && result.hasMain && result.refreshReady) break;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  results.push({ route, ...result });
  await new Promise((resolve) => setTimeout(resolve, 500));
}

socket.close();
const failures = results.filter(
  (result) => result.label !== result.expected || !result.hasMain || !result.refreshReady || result.overflow || result.errorPanel,
);
console.log(JSON.stringify({ results, browserErrors }, null, 2));
if (failures.length > 0 || browserErrors.length > 0) {
  throw new Error("One or more routes failed runtime verification.");
}
