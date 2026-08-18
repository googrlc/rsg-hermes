#!/usr/bin/env node
/**
 * Fill the EXISTING Zoho Creator app Renewals Desk (renewals-desk).
 * Never creates a new application. Never publishes production.
 *
 * Live IDE (verified): Design | Workflow | Settings are buttons, not tabs.
 * Smart Chat is the bottom bar "Here is your Smart Chat (Ctrl+Space)".
 * A "Upgrade to Creator 5" modal can sit on top of the IDE — dismiss
 * "Upgrade later from Setup" before any other click.
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "../..");
const DESK_DOCS = path.join(REPO, "docs/zoho/creator-renewals-desk");
const PROFILE = path.join(__dirname, ".pw-profile");
const SHOTS = process.env.SHOTS_DIR || "/opt/cursor/artifacts";
const WORKSPACE = "lamar_risksolutionsgroup668";
const APP = "renewals-desk";
const BUILDER =
  process.env.ZOHO_BUILDER_URL ||
  `https://creator.zoho.com/appbuilder/${WORKSPACE}/${APP}/edit`;

const CRM_MODULES = [
  "Accounts",
  "Deals",
  "Policies",
  "Renewal_Events",
  "Renewals",
  "AMS_Write_Queue",
  "Tasks",
];

function readDoc(...parts) {
  return fs.readFileSync(path.join(DESK_DOCS, ...parts), "utf8");
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function shot(page, name) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, `pw_${name}.png`);
  await page.screenshot({ path: file, fullPage: false }).catch(() => {});
  console.log(`screenshot ${file}`);
}

async function dumpUi(page, name) {
  const texts = await page.evaluate(() => {
    const take = (sel) =>
      [...document.querySelectorAll(sel)]
        .map((el) => (el.innerText || el.getAttribute("aria-label") || el.title || "").trim())
        .filter((t) => t && t.length < 80)
        .slice(0, 100);
    return {
      url: location.href,
      title: document.title,
      buttons: take("button, [role='button'], a"),
      headings: take("h1,h2,h3,[role='tab'],[role='dialog']"),
    };
  }).catch(() => ({ url: page.url(), title: "", buttons: [], headings: [] }));
  const dest = path.join(SHOTS, `pw_${name}_ui.json`);
  fs.writeFileSync(dest, JSON.stringify(texts, null, 2));
  console.log("ui dump", dest, "buttons", texts.buttons.slice(0, 20));
  return texts;
}

async function evalClick(page, labels) {
  return page.evaluate((want) => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const nodes = [...document.querySelectorAll("button, [role='button'], a, span, div")];
    for (const label of want) {
      const hit = nodes.find((el) => {
        const t = norm(el.innerText);
        const a = norm(el.getAttribute("aria-label"));
        const title = norm(el.title);
        return t === label || a === label || title === label || t.startsWith(label);
      });
      if (hit) {
        hit.click();
        return label;
      }
    }
    return null;
  }, labels);
}

async function clickFirst(page, locators, timeout = 4000) {
  for (const loc of locators) {
    try {
      const el = typeof loc === "string" ? page.locator(loc).first() : loc.first ? loc.first() : loc;
      await el.waitFor({ state: "visible", timeout });
      await el.click({ timeout: 5000 });
      return true;
    } catch {
      /* next */
    }
  }
  return false;
}

async function dismissOverlays(page) {
  for (let i = 0; i < 4; i++) {
    const hit = await evalClick(page, [
      "Upgrade later from Setup",
      "Don't show again",
      "Skip",
      "Not now",
      "Maybe later",
      "close",
      "Close",
    ]);
    if (hit) {
      console.log("dismissed overlay", hit);
      await sleep(700);
      await shot(page, `overlay_${i}`);
    } else {
      await page.keyboard.press("Escape").catch(() => {});
      break;
    }
  }
}

function ideReady(dump) {
  const blob = `${(dump.buttons || []).join(" | ")} ${(dump.headings || []).join(" | ")}`;
  return /Access Development Live/i.test(blob) || /Smart Chat/i.test(blob);
}

async function waitForIde(page) {
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    await dismissOverlays(page);
    const dump = await dumpUi(page, "wait_ide");
    if (ideReady(dump)) {
      await dismissOverlays(page);
      await shot(page, "ide_ready");
      await dumpUi(page, "ide_ready");
      return;
    }
    await sleep(2500);
  }
  await shot(page, "ide_timeout");
  throw new Error("IDE did not become ready (Access Development Live / Smart Chat)");
}

async function openExistingDesk(page) {
  console.log("open", BUILDER);
  await page.goto(BUILDER, { waitUntil: "domcontentloaded", timeout: 120000 });
  await shot(page, "01_nav");
  if (page.url().includes("accounts.zoho.com")) {
    console.log("session expired — sign-in page");
    await page.waitForURL((u) => !u.toString().includes("accounts.zoho.com"), {
      timeout: process.env.ZOHO_LOGIN_WAIT === "1" ? 600000 : 90000,
    });
    await page.goto(BUILDER, { waitUntil: "domcontentloaded", timeout: 120000 });
  }
  await waitForIde(page);
  console.log("ide", page.url());
  if (/new application/i.test(await page.title())) {
    throw new Error("Landed on New application. Abort.");
  }
}

async function openPlusMenu(page) {
  const hit = await evalClick(page, ["+"]);
  if (hit) {
    await sleep(800);
    await shot(page, "plus_menu");
    await dumpUi(page, "plus_menu");
    return true;
  }
  return clickFirst(page, [
    page.getByRole("button", { name: /^\+$/ }),
    "[aria-label='Create']",
    "[title='Create']",
    "button:has-text('+')",
  ]);
}

async function addCrmIntegrations(page) {
  console.log("CRM integrations via + Form");
  await evalClick(page, ["Design"]);
  await sleep(500);
  const opened = await openPlusMenu(page);
  if (!opened) {
    console.log("plus menu not found");
    return;
  }
  const formHit =
    (await evalClick(page, ["Form", "Create Form", "New Form"])) ||
    (await clickFirst(page, ["text=Form", "button:has-text('Form')"]));
  await sleep(1000);
  await shot(page, "create_form");
  await dumpUi(page, "create_form");
  if (!formHit) return;

  const crmHit = await clickFirst(page, [
    "text=Using an Integrated Datasource",
    "text=Zoho CRM",
    "text=Import from Zoho CRM",
    "text=Other Zoho apps",
    "text=Other Zoho Apps",
    "text=Integrations",
    "text=Import data",
    "text=Import Data",
    "text=CRM",
  ]);
  if (!crmHit) {
    const ev = await evalClick(page, [
      "Zoho CRM",
      "Import from Zoho CRM",
      "Other Zoho apps",
      "Other Zoho Apps",
      "Integrations",
      "Import data",
      "Import Data",
    ]);
    console.log("crm picker eval", ev);
  }
  await sleep(1200);
  await shot(page, "crm_picker");
  await dumpUi(page, "crm_picker");

  for (const mod of CRM_MODULES) {
    const row = page.getByText(mod, { exact: true });
    if (await row.first().isVisible().catch(() => false)) {
      await row.first().click();
      console.log("checked", mod);
    }
  }
  await clickFirst(page, [
    "button:has-text('Create')",
    "button:has-text('Import')",
    "button:has-text('Next')",
    "button:has-text('Done')",
    "button:has-text('Add')",
  ]);
  await shot(page, "crm_done");
}

async function createHtmlPage(page, displayName, htmlPath) {
  console.log("create page", displayName);
  await evalClick(page, ["Design"]);
  await sleep(400);
  await openPlusMenu(page);
  const hit =
    (await evalClick(page, ["Page", "Create Page", "New Page"])) ||
    (await clickFirst(page, ["text=Page", "button:has-text('Page')"]));
  await shot(page, `page_${displayName}_menu`);
  await dumpUi(page, `page_${displayName}_menu`);
  if (!hit) {
    console.log("Page item missing in plus menu");
    return;
  }
  await sleep(800);
  const nameBox = page.getByPlaceholder(/page name|untitled|name/i).or(page.getByLabel(/page name|name/i));
  if (await nameBox.first().isVisible().catch(() => false)) {
    await nameBox.first().fill(displayName);
  } else {
    await page.keyboard.type(displayName);
  }
  await clickFirst(page, ["text=HTML", "text=Custom HTML", "text=Code", "text=Blank", "text=HTML snippet"]);
  const html = fs.readFileSync(htmlPath, "utf8");
  const editor = page.locator("textarea:visible, .CodeMirror textarea, [contenteditable='true']:visible").last();
  if (await editor.isVisible().catch(() => false)) {
    await editor.click();
    await page.keyboard.press("Control+A");
    await page.keyboard.insertText(html);
  }
  await clickFirst(page, ["button:has-text('Save')", "button:has-text('Create')", "button:has-text('Done')"]);
  await shot(page, `page_${displayName}_saved`);
}

async function pasteZia(page) {
  // Ctrl+Space is Zoho Cliq (contacts/channels), not Creator Zia.
  // Creator form AI is + → Form → Using Zia. Do not paste the spec pack here.
  console.log("skip Cliq Smart Chat; Creator Zia is + Form Using Zia");
  await shot(page, "zia_skipped_cliq");
}

async function main() {
  fs.mkdirSync(PROFILE, { recursive: true });
  fs.mkdirSync(SHOTS, { recursive: true });
  const step = (process.argv.find((a) => a.startsWith("--step=")) || "--step=all").split("=")[1];
  const cdp = process.env.ZOHO_CDP;

  let context;
  let page;
  if (cdp) {
    const browser = await chromium.connectOverCDP(cdp);
    context = browser.contexts()[0] || (await browser.newContext());
    page = context.pages()[0] || (await context.newPage());
  } else {
    context = await chromium.launchPersistentContext(PROFILE, {
      channel: "chrome",
      headless: process.env.PW_HEADLESS === "1",
      viewport: { width: 1440, height: 900 },
      ignoreHTTPSErrors: true,
    });
    page = context.pages()[0] || (await context.newPage());
  }
  page.setDefaultTimeout(15000);

  try {
    await openExistingDesk(page);
    if (step === "open") {
      console.log("open-only", page.url());
      return;
    }
    if (step === "all" || step === "zia") {
      await pasteZia(page);
    }
    if (step === "all" || step === "crm") {
      await addCrmIntegrations(page);
    }
    if (step === "all" || step === "pages") {
      await createHtmlPage(page, "Desk", path.join(DESK_DOCS, "pages/desk.html"));
      await createHtmlPage(page, "Card", path.join(DESK_DOCS, "pages/card.html"));
    }
    await evalClick(page, ["Design"]);
    await shot(page, "99_final");
    await dumpUi(page, "99_final");
    console.log("done", page.url());
  } catch (err) {
    await shot(page, "error");
    await dumpUi(page, "error").catch(() => {});
    console.error(err);
    process.exitCode = 1;
  } finally {
    if (!cdp && process.env.PW_KEEP !== "1") {
      await context.close();
    }
  }
}

main();
