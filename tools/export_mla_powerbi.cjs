#!/usr/bin/env node
const fs = require("node:fs/promises");
const fsSync = require("node:fs");
const path = require("node:path");

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (firstError) {
    const fallbackRoot = process.env.PLAYWRIGHT_NODE_MODULES;
    if (fallbackRoot) {
      try {
        return require(path.join(fallbackRoot, "playwright"));
      } catch (_) {
        // Fall through to the local Chrome fallback message below.
      }
    }
    throw firstError;
  }
}

function parseArgs(argv) {
  const args = argv.slice(2);
  const mode = args[0];
  if (!["--all-dates", "--current"].includes(mode)) {
    throw new Error("Usage: node tools/export_mla_powerbi.cjs (--all-dates|--current) <url> <output.csv>");
  }
  const targetUrl = args[1];
  const outputPath = args[2];
  if (!targetUrl || !outputPath) {
    throw new Error("Usage: node tools/export_mla_powerbi.cjs (--all-dates|--current) <url> <output.csv>");
  }
  return { mode, targetUrl, outputPath };
}

function launchOptions() {
  const opts = {
    headless: true,
    args: ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
  };
  const candidates = [
    process.env.CHROME_PATH,
    process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : "",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ].filter(Boolean);
  const chromePath = candidates.find((candidate) => fsSync.existsSync(candidate));
  if (chromePath) {
    opts.executablePath = chromePath;
  }
  return opts;
}

async function exportAllDates(page) {
  return page.evaluate(async () => {
    const container = document.getElementById("report-container");
    const report = window.powerbi.get(container);
    const models = window["powerbi-client"].models;

    const shouldRemove = (filter) => {
      const target = filter && filter.target;
      if (!target) return false;
      return target.table === "Calendar" && ["Date", "Latest Report Date"].includes(target.column);
    };

    const cleanOwner = async (owner, label, log) => {
      try {
        const filters = await owner.getFilters();
        const kept = filters.filter((filter) => !shouldRemove(filter));
        if (kept.length !== filters.length) {
          await owner.setFilters(kept);
          log.push(`${label}: removed ${filters.length - kept.length} date filters`);
        }
      } catch (error) {
        log.push(`${label}: ${error.message}`);
      }
    };

    const log = [];
    await cleanOwner(report, "report", log);
    const pages = await report.getPages();
    const activePage = pages.find((item) => item.isActive) || pages[0];
    await activePage.setActive();
    await cleanOwner(activePage, "page", log);

    const visuals = await activePage.getVisuals();
    for (const visual of visuals) {
      await cleanOwner(visual, `visual:${visual.title || visual.name}`, log);
      if (visual.type !== "slicer") continue;
      try {
        const state = await visual.getSlicerState();
        const targets = state.targets || [];
        if (targets.some((target) => target.table === "Calendar" && ["Date", "Latest Report Date"].includes(target.column))) {
          await visual.setSlicerState({ filters: [] });
          log.push(`slicer:${visual.title || visual.name}: cleared`);
        }
      } catch (error) {
        log.push(`slicer:${visual.title || visual.name}: ${error.message}`);
      }
    }

    await new Promise((resolve) => setTimeout(resolve, 5000));
    const refreshedVisuals = await activePage.getVisuals();
    const chart =
      refreshedVisuals.find((visual) => visual.title === "Data Chart") ||
      refreshedVisuals.find((visual) => visual.title === "Data Table");
    if (!chart) {
      throw new Error("No Data Chart/Data Table visual found in MLA PowerBI report");
    }

    const data = await chart.exportData(models.ExportDataType.Summarized);
    return {
      data: data.data,
      log,
      visual: { title: chart.title, name: chart.name, type: chart.type },
    };
  });
}

async function exportCurrent(page) {
  return page.evaluate(async () => {
    const container = document.getElementById("report-container");
    const report = window.powerbi.get(container);
    const models = window["powerbi-client"].models;
    const pages = await report.getPages();
    const activePage = pages.find((item) => item.isActive) || pages[0];
    await activePage.setActive();
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const visuals = await activePage.getVisuals();
    const chart =
      visuals.find((visual) => visual.title === "Data Chart") ||
      visuals.find((visual) => visual.title === "Data Table");
    if (!chart) {
      throw new Error("No Data Chart/Data Table visual found in MLA PowerBI report");
    }
    const data = await chart.exportData(models.ExportDataType.Summarized);
    return {
      data: data.data,
      log: ["used report default filters"],
      visual: { title: chart.title, name: chart.name, type: chart.type },
    };
  });
}

async function main() {
  const { mode, targetUrl, outputPath } = parseArgs(process.argv);
  const { chromium } = loadPlaywright();
  await fs.mkdir(path.dirname(outputPath), { recursive: true });

  const browser = await chromium.launch(launchOptions(chromium));
  const page = await browser.newPage({
    acceptDownloads: true,
    viewport: { width: 1440, height: 1000 },
  });
  page.setDefaultTimeout(120000);

  const messages = [];
  page.on("console", (msg) => messages.push(`[${msg.type()}] ${msg.text()}`));
  page.on("pageerror", (err) => messages.push(`[pageerror] ${err.message}`));

  try {
    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
    await page.waitForSelector("#export_button", { state: "visible", timeout: 120000 });
    await page.waitForTimeout(8000);
    const result = mode === "--all-dates" ? await exportAllDates(page) : await exportCurrent(page);
    await fs.writeFile(outputPath, result.data);
    const stat = await fs.stat(outputPath);
    console.log(
      JSON.stringify({
        ok: true,
        outPath: outputPath,
        bytes: stat.size,
        visual: result.visual,
        log: result.log,
      })
    );
  } catch (error) {
    const errorPath = path.join(path.dirname(outputPath), "mla_export_error.html");
    await fs.writeFile(errorPath, await page.content().catch(() => ""));
    console.error(
      JSON.stringify({
        ok: false,
        error: error.message,
        errorPath,
        messages: messages.slice(-20),
      })
    );
    process.exitCode = 1;
  } finally {
    await browser.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
});
