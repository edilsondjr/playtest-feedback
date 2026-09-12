// Headless gate for the playtest feedback form.
//   node tools/test_form.mjs
// Loads index.html in jsdom, exercises the required-field validation and the
// report generation. Exit code 0 = all checks passed.
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

function load(search = "") {
  pageErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => pageErrors.push(e.message));
  virtualConsole.on("error", (m) => pageErrors.push(String(m)));
  const dom = new JSDOM(html, {
    url: PAGE_URL + search,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    virtualConsole,
  });
  const { document } = dom.window;
  const submit = () => {
    document.getElementById("form").dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true })
    );
  };
  return { dom, document, submit };
}

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
  document.getElementById("what").value = "Game froze on the third station.";
  document.getElementById("steps").value = "1. Start run\n2. Pick coal\n3. Frozen screen";
  document.getElementById("device").value = "Windows 11 / i5-9400F / 16 GB";
  document.getElementById("version").value = "v0.2.0";
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

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
