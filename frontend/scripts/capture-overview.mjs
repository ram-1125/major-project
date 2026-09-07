import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const [widthText, heightText, outputPath] = process.argv.slice(2);
const width = Number(widthText);
const height = Number(heightText);
if (!Number.isFinite(width) || !Number.isFinite(height) || !outputPath) {
  throw new Error("Usage: node scripts/capture-overview.mjs WIDTH HEIGHT OUTPUT");
}

const deadline = Date.now() + 60_000;
let target;
while (Date.now() < deadline) {
  try {
    const targets = await fetch("http://127.0.0.1:9222/json").then((response) => response.json());
    target = targets.find((item) => item.type === "page");
    if (target) break;
  } catch {
    // Edge is still opening its local debugging endpoint.
  }
  await new Promise((resolve) => setTimeout(resolve, 250));
}
if (!target) throw new Error("Could not find the local Edge page target.");

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let identifier = 0;
const pending = new Map();
const runtimeIssues = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data));
  if (message.method === "Runtime.exceptionThrown") {
    runtimeIssues.push(message.params?.exceptionDetails?.text ?? "Browser exception");
  }
  if (message.method === "Runtime.consoleAPICalled" && message.params?.type === "error") {
    runtimeIssues.push(
      message.params.args?.map((argument) => argument.value ?? argument.description).join(" ") ?? "Console error",
    );
  }
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message));
  else resolve(message.result);
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
await command("Page.navigate", { url: "http://localhost:5173/#/overview" });

let ready = false;
while (Date.now() < deadline) {
  const result = await command("Runtime.evaluate", {
    expression: `Boolean(document.querySelector('.overview-command')) && Boolean(document.querySelector('.overview-pipeline-ring')) && !Boolean(document.querySelector('.state-panel--error'))`,
    returnByValue: true,
  });
  if (result.result.value === true) {
    ready = true;
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!ready) throw new Error("Overview did not finish loading genuine local data.");

await new Promise((resolve) => setTimeout(resolve, 750));
const verification = await command("Runtime.evaluate", {
  expression: `(() => {
    const root = document.documentElement;
    const rings = [...document.querySelectorAll('.overview-pipeline-ring')];
    return {
      horizontalOverflow: root.scrollWidth > root.clientWidth,
      ringCount: rings.length,
      everyRingContainsIcon: rings.every((ring) => Boolean(ring.querySelector('svg'))),
      separatePipelineIconTiles: document.querySelectorAll('.pipeline-stage__icon').length,
      kpiCount: document.querySelectorAll('.overview-kpi').length,
    };
  })()`,
  returnByValue: true,
});
const result = verification.result.value;
if (
  result.horizontalOverflow ||
  result.ringCount !== 6 ||
  !result.everyRingContainsIcon ||
  result.separatePipelineIconTiles !== 0 ||
  result.kpiCount !== 6 ||
  runtimeIssues.length > 0
) {
  throw new Error(`Overview browser verification failed: ${JSON.stringify({ ...result, runtimeIssues })}`);
}
const screenshot = await command("Page.captureScreenshot", {
  format: "png",
  fromSurface: true,
  captureBeyondViewport: false,
});
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, Buffer.from(screenshot.data, "base64"));
console.log(JSON.stringify({ width, height, ...result, runtimeIssues }));
socket.close();
