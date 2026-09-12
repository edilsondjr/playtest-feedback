// Headless gate for the playtest feedback form.
//   node tools/test_form.mjs
// Loads index.html in jsdom, exercises required-field validation, report
// generation AND the delivery wiring (does the page really POST the report to
// the configured destination, and does it surface failures?). Exit 0 = all
// checks passed. No network access: fetch is stubbed.
import { readFileSync } from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const PAGE_URL = "https://edilsondjr.github.io/playtest-feedback/";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  -- " + detail : ""}`);
}

let pageErrors = [];

// Stub fetch on every load: jsdom has no fetch, and the form calls it on submit.
// `impl` lets a test decide how the destination answers (ok / HTTP error).
function load(search = "", impl = null) {
  pageErrors = [];
  const calls = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => pageErrors.push(e.message));
  virtualConsole.on("error", (m) => pageErrors.push(String(m)));
  const dom = new JSDOM(html, {
    url: PAGE_URL + search,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      window.fetch = (url, opts = {}) => {
        calls.push({ url, opts });
        const answer = impl ? impl(url, opts)
                            : Promise.resolve({ ok: true, status: 200 });
        return Promise.resolve(answer);
      };
    },
  });
  const { document } = dom.window;
  const submit = () => {
    document.getElementById("form").dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true })
    );
  };
  return { dom, document, submit, calls };
}

function fill(document, over = {}) {
  const values = Object.assign({
    what: "Game froze on the third station.",
    steps: "1. Start run\n2. Pick coal\n3. Frozen screen",
    device: "Windows 11 / i5-9400F / 16 GB",
    version: "v0.2.0",
    shot: "https://example.com/shot.png",
    contact: "tester@example.com",
  }, over);
  for (const [id, value] of Object.entries(values)) {
    document.getElementById(id).value = value;
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// 1. required fields exist with the required attribute
{
  const { document } = load();
  for (const id of ["what", "steps", "device", "version"]) {
    const el = document.getElementById(id);
    check(`required field "${id}" present`, !!el, el ? "" : "element missing");
    check(`required field "${id}" is marked required`, !!(el && el.hasAttribute("required")));
  }
  check("screenshot link field present (optional)", !!document.getElementById("shot"));
  check("screenshot field is NOT required",
    !document.getElementById("shot").hasAttribute("required"));
}

// 2. empty submit is blocked and names the missing fields
{
  const { document, submit } = load();
  submit();
  const msg = document.getElementById("msg");
  check("empty submit blocked (report stays hidden)",
    document.getElementById("report").style.display !== "block");
  check("empty submit names missing fields",
    /Required field/.test(msg.textContent) && /steps to reproduce/.test(msg.textContent),
    msg.textContent.slice(0, 90));
}

// 3. filled submit produces the structured report
{
  const { document, submit } = load();
  fill(document, { shot: "", contact: "" });
  submit();
  const text = document.getElementById("report_text").value;
  check("filled submit shows the report",
    document.getElementById("report").style.display === "block");
  for (const [name, needle] of [
    ["build", "v0.2.0"],
    ["device", "i5-9400F"],
    ["what happened", "Game froze on the third station."],
    ["steps", "3. Frozen screen"],
  ]) {
    check(`report carries ${name}`, text.includes(needle));
  }
  check("report marks screenshot as not provided", text.includes("(none provided)"));
  check("no uncaught page errors during the flow", pageErrors.length === 0,
    pageErrors.join(" | ").slice(0, 140));
}

// 4. ?v= prefills the build field
{
  const { document } = load("?v=0.3.1");
  check("?v= pre-fills the build field",
    document.getElementById("version").value === "0.3.1",
    "got '" + document.getElementById("version").value + "'");
}

// 5. the destination is configured and the page really posts to it
const endpoint = (html.match(/var\s+FEEDBACK_ENDPOINT\s*=\s*"([^"]*)"/) || [])[1] || "";
check("FEEDBACK_ENDPOINT is configured", /^https:\/\/\S+$/.test(endpoint),
  endpoint || "empty - reports would only reach the tester");
check("endpoint is not a secret-bearing URL (page is public)",
  !!endpoint && !/[?&](token|key|secret)=/i.test(endpoint), endpoint);
{
  const { document, submit, calls } = load();
  fill(document);
  submit();
  await tick();
  check("submit posts exactly once", calls.length === 1, `calls=${calls.length}`);
  const call = calls[0] || { url: undefined, opts: {} };
  check("posts to the configured destination", call.url === endpoint, String(call.url));
  check("uses POST with a JSON content type",
    call.opts.method === "POST" &&
    /application\/json/i.test((call.opts.headers || {})["Content-Type"] || ""),
    call.opts.method);
  let body = {};
  try { body = JSON.parse(call.opts.body); } catch (e) { /* leave body empty */ }
  const wanted = ["what_happened", "repro_steps", "device", "build", "screenshot_url",
                  "severity", "contact", "report_markdown"];
  check("body carries all 8 report fields",
    wanted.every((k) => k in body), Object.keys(body).join(","));
  check("body mirrors the typed values",
    body.what_happened === "Game froze on the third station." &&
    body.device === "Windows 11 / i5-9400F / 16 GB" &&
    body.build === "v0.2.0" &&
    body.report_markdown === document.getElementById("report_text").value);
  check("accepted send is reported as sent",
    /Sent\./.test(document.getElementById("msg").textContent),
    document.getElementById("msg").textContent.slice(0, 80));
  check("no uncaught page errors while posting", pageErrors.length === 0,
    pageErrors.join(" | ").slice(0, 140));
}

// 6. a failing destination is surfaced, never silent
{
  const { document, submit } = load("", () => Promise.resolve({ ok: false, status: 500 }));
  fill(document);
  submit();
  await tick();
  const msg = document.getElementById("msg").textContent;
  check("HTTP failure is shown with its status", /Send failed \(HTTP 500\)/.test(msg),
    msg.slice(0, 90));
  check("failure falls back to copy/download hint",
    /copy|download/i.test(document.getElementById("send_hint").innerHTML));
  check("failure keeps the report on screen for the tester",
    document.getElementById("report_text").value.length > 100);
}

// 7. a rejected request (offline) is surfaced too
{
  const { document, submit } = load("", () => Promise.reject(new Error("NetworkError")));
  fill(document);
  submit();
  await tick();
  check("network failure is shown to the tester",
    /Send failed \(NetworkError\)/.test(document.getElementById("msg").textContent),
    document.getElementById("msg").textContent.slice(0, 90));
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
