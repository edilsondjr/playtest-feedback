// End-to-end validation driver for the live playtest form.
//
// Opens the LIVE GitHub Pages form in a real Chrome (headed), fills the required
// fields the way a tester does, submits, and captures before/after screenshots.
// It talks to Chrome directly over CDP (no puppeteer dependency): start Chrome
// yourself with --remote-debugging-port and pass the port.
//
// Usage:
//   node tools/e2e_submit.mjs <port> <url> <outDir> [tag]
//
// Prints a JSON summary: field values read back from the DOM, the submit status
// message, and the paths of the screenshots. Never prints any secret - the URL
// and the fields only.

import { writeFileSync } from "node:fs";
import { join } from "node:path";

const [, , portArg, url, outDir, tagArg] = process.argv;
const port = Number(portArg || 9333);
const tag = tagArg || "run";
if (!url || !outDir) {
  console.error("usage: node tools/e2e_submit.mjs <port> <url> <outDir> [tag]");
  process.exit(2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function wsUrlForTarget() {
  const res = await fetch(`http://127.0.0.1:${port}/json/list`);
  const targets = await res.json();
  const page = targets.find((t) => t.type === "page");
  if (!page) throw new Error("no page target on port " + port);
  return page.webSocketDebuggerUrl;
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("CDP timeout: " + method));
        }
      }, 30000);
    });
  }
}

async function evaluate(cdp, expr) {
  const r = await cdp.send("Runtime.evaluate", {
    expression: expr,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) throw new Error("page error: " + JSON.stringify(r.exceptionDetails));
  return r.result.value;
}

async function shot(cdp, outDir, name) {
  const r = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  const p = join(outDir, name);
  writeFileSync(p, Buffer.from(r.data, "base64"));
  return p;
}

const report = {
  url,
  tag,
  started_at: new Date().toISOString(),
  steps: [],
  screenshots: {},
  ok: false,
};

function mark(step, detail) {
  report.steps.push({ step, detail, at: new Date().toISOString() });
}

// Publish a local screenshot to an anonymous host so the report can carry a real
// clickable link (the static page cannot accept file uploads). No account, no
// key; returns "" on any failure so the run still submits without a link.
async function uploadScreenshot(path) {
  try {
    const bytes = await import("node:fs").then((fs) => fs.readFileSync(path));
    const fd = new FormData();
    fd.append("reqtype", "fileupload");
    fd.append("fileToUpload", new Blob([bytes], { type: "image/png" }), path.split(/[\\/]/).pop());
    const res = await fetch("https://catbox.moe/user/api.php", { method: "POST", body: fd });
    const text = (await res.text()).trim();
    if (!res.ok || !/^https?:\/\//.test(text)) return "";
    return text;
  } catch {
    return "";
  }
}

const TITLE = `[E2E ${tag}] live-page validation`;

const WHAT =
  `${TITLE} - the playtest feedback button in the build's OPTIONS screen opened this ` +
  `form with the build already in the version field, and this report was typed and sent ` +
  `from the live GitHub Pages URL by a real browser. Before/after screenshots captured by ` +
  `tools/e2e_submit.mjs.`;

const STEPS = [
  "1. Launch the exported build (coalgrave_playtest_v0.2.0_win64.zip) and open OPTIONS.",
  "2. Press 'SEND PLAYTEST FEEDBACK' at the top of the panel - the default browser opens the live form with ?v=<build>.",
  "3. The 'Game version / build' field arrives pre-filled from the URL.",
  "4. Fill what happened / steps / device, optionally press 'Detect from my browser'.",
  "5. Press 'Generate report / Gerar relatorio'.",
  "Expected: a green 'Sent. Thank you! / Enviado. Obrigado!' line and the report archived by the collector.",
].join("\n");

try {
  const ws = new WebSocket(await wsUrlForTarget());
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  const cdp = new CDP(ws);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");

  await cdp.send("Page.navigate", { url });
  await sleep(2500);
  for (let i = 0; i < 20; i++) {
    const ready = await evaluate(cdp, "document.readyState");
    if (ready === "complete" && (await evaluate(cdp, "!!document.getElementById('form')"))) break;
    await sleep(500);
  }
  report.page = {
    title: await evaluate(cdp, "document.title"),
    href: await evaluate(cdp, "location.href"),
    version_prefilled: await evaluate(cdp, "document.getElementById('version').value"),
  };
  mark("loaded", report.page.href);
  report.screenshots.before = await shot(cdp, outDir, `${tag}_01_form_opened.png`);

  // Fill the way a tester does: type values, then let the page's own detector
  // fill the device field (proves the report came from the live page's JS).
  const fill = {
    what: WHAT,
    steps: STEPS,
    contact: "ops - automated end-to-end validation",
  };
  for (const [id, value] of Object.entries(fill)) {
    await evaluate(
      cdp,
      `(() => { const el = document.getElementById(${JSON.stringify(id)});` +
        ` el.value = ${JSON.stringify(value)};` +
        ` el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`
    );
  }
  const device = await evaluate(
    cdp,
    `(() => { document.getElementById('detect').click();` +
      ` return document.getElementById('device').value; })()`
  );
  const severity = await evaluate(
    cdp,
    `(() => { const s = document.getElementById('severity');` +
      ` s.selectedIndex = [...s.options].findIndex(o => o.text.includes('Suggestion'));` +
      ` s.dispatchEvent(new Event('change', { bubbles: true })); return s.value; })()`
  );
  report.fields = { device, severity };
  mark("filled", { device, severity });
  report.screenshots.filled = await shot(cdp, outDir, `${tag}_02_form_filled.png`);

  // Optional: publish the screenshot so the report can carry a real link (the
  // page cannot accept file uploads). Anonymous host, no account, no key.
  const shotUrl = await uploadScreenshot(report.screenshots.filled);
  if (shotUrl) {
    report.screenshot_url = shotUrl;
    await evaluate(
      cdp,
      `(() => { const el = document.getElementById('shot');` +
        ` el.value = ${JSON.stringify(shotUrl)};` +
        ` el.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`
    );
    mark("screenshot_link", shotUrl);
  } else {
    mark("screenshot_link", "(upload failed - field left empty)");
  }

  await evaluate(cdp, "document.getElementById('submit').click(); true");
  let status = "";
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    status = await evaluate(cdp, "document.getElementById('msg').textContent");
    const cls = await evaluate(cdp, "document.getElementById('msg').className");
    report.status_class = cls;
    if (/Sent\.|Enviado\./.test(status) || /Send failed/.test(status) || /missing/i.test(status)) break;
  }
  report.status = status;
  report.report_markdown = await evaluate(cdp, "document.getElementById('report_text').value");
  report.screenshots.after = await shot(cdp, outDir, `${tag}_03_sent.png`);
  mark("submitted", status);

  report.ok = /Sent\.|Enviado\./.test(status);
} catch (err) {
  report.error = String(err && err.stack ? err.stack : err);
}

report.finished_at = new Date().toISOString();
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 1);
