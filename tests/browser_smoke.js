/*
 * Headless smoke test for the browser app.
 *
 * Loads emailrecon.html in Chromium, runs Demo mode, and asserts the app
 * registered its sites, filled the table, found hits, and threw no page
 * errors. Run with:  node tests/browser_smoke.js   (needs puppeteer)
 */
"use strict";

const path = require("path");
const fs = require("fs");

// In CI we `npm install puppeteer` (bundled Chromium). Locally we may only have
// puppeteer-core, so fall back to a system browser when present.
let puppeteer;
const launchOpts = { headless: "new", args: ["--no-sandbox", "--disable-setuid-sandbox"] };
try {
  puppeteer = require("puppeteer");
} catch (e) {
  puppeteer = require("puppeteer-core");
  const candidates = [
    process.env.CHROME_PATH,
    "/usr/local/bin/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
  ].filter(Boolean);
  launchOpts.executablePath = candidates.find((c) => {
    try { return fs.existsSync(c); } catch (err) { return false; }
  });
  if (!launchOpts.executablePath) {
    console.error("No browser found — set CHROME_PATH or install puppeteer");
    process.exit(1);
  }
}

(async () => {
  const browser = await puppeteer.launch(launchOpts);
  const page = await browser.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("dialog", (d) => d.accept());

  const appPath = path.resolve(__dirname, "..", "emailrecon.html");
  await page.goto("file://" + appPath, { waitUntil: "load" });

  const sites = await page.evaluate(() => SITES.length);
  if (sites < 116) throw new Error(`expected >= 116 sites, got ${sites}`);

  await page.select("#mode", "demo");
  await page.type("#email", "jane.doe@example.com");
  await page.click("#run");
  await page.waitForFunction(
    () => document.getElementById("status").textContent.startsWith("Done"),
    { timeout: 30000 }
  );

  const rows = await page.$$eval("#tbl tbody tr", (t) => t.length);
  const found = await page.$$eval(
    "#tbl tbody tr",
    (t) => t.filter((x) => x.dataset.status === "registered").length
  );
  const confCells = await page.$$eval("#tbl tbody tr td:nth-child(6)", (t) => t.length);

  await browser.close();

  if (rows !== sites) throw new Error(`table rows ${rows} != sites ${sites}`);
  if (found === 0) throw new Error("demo mode produced no hits");
  if (confCells !== sites) throw new Error(`confidence cells ${confCells} != sites ${sites}`);
  if (pageErrors.length) throw new Error("page errors: " + pageErrors.join("; "));

  console.log(`browser smoke OK: ${sites} sites, ${rows} rows, ${found} hits, 0 page errors`);
})().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
