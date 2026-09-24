// f-20 page census harness. Usage: node census.mjs <stack> <port> <outdir>
// Renders every route in Chromium against a server whose SITE_DATA reads go to PRODUCTION /data/.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
const require = createRequire("D:/temp/f20/s2/package.json");
const { chromium } = require("playwright");

const [stack, port, outdir] = process.argv.slice(2);
const BASE = `http://localhost:${port}`;
fs.mkdirSync(path.join(outdir, "shots"), { recursive: true });
const PHRASES = JSON.parse(fs.readFileSync("D:/temp/f20/s2/lib/bannedPhrases.json", "utf8"));
const METRICS = JSON.parse(fs.readFileSync("D:/temp/f20/metrics.json", "utf8"));
const TEAMS = JSON.parse(fs.readFileSync("D:/temp/f20/nman.json", "utf8")).teams.map((t) => t.slug);

const core = ["/", "/about", "/method", "/research", "/log", "/studies/market-calibration",
  "/nfl", "/nfl/players", "/nfl/teams", "/nfl/fantasy", "/nfl/analytics", "/nfl/live", "/nfl/news", "/nfl/sources",
  "/cfb", "/cfb/players", "/cfb/teams", "/cfb/analytics", "/cfb/live", "/nba", "/mlb", "/nhl",
  "/nfl/board", "/nfl/board/scorecard", "/nfl/board?tab=streaks", "/nfl/board?tab=tracker", "/nfl/board?tab=games",
];
const players = ["a-j-brown", "ja-marr-chase", "justin-jefferson", "christian-mccaffrey", "patrick-mahomes", "travis-kelce",
  "jerry-rice", "ashton-jeanty", "bijan-robinson", "saquon-barkley", "josh-allen"].map((s) => `/nfl/player/${s}`);
const teams4 = ["kc", "buf", "ari"].map((s) => `/nfl/team/${s}`);
const metrics4 = ["usage_stability.between.target_share", "pace.plays_per_game", "schedule.opportunity.wr"].map((m) => `/nfl/analytics/${m}`);
const full = [...core, ...players, ...teams4, ...metrics4];
const once = [...TEAMS.map((s) => `/nfl/team/${s}`).filter((p) => !teams4.includes(p)),
  ...METRICS.map((m) => `/nfl/analytics/${m}`).filter((p) => !metrics4.includes(p))];

const ONLY = process.env.ROUTES ? process.env.ROUTES.split(",") : null;
const jobs = [];
for (const r of (ONLY ?? full)) for (const w of [1280, 390]) for (const g of ["navy", "paper"]) jobs.push({ route: r, width: w, ground: g, shot: true });
if (!ONLY) for (const r of once) jobs.push({ route: r, width: 1280, ground: "navy", shot: false });

function lint(text, board) {
  const hits = PHRASES.sitewide.map((p) => new RegExp(p, "i").exec(text)?.[0]).filter(Boolean);
  if (!board) return hits;
  let t = text;
  for (const a of PHRASES.boardAllowed) t = t.replace(new RegExp(a.pattern, "gi"), " ");
  return [...hits, ...PHRASES.boardOnly.map((p) => new RegExp(p, "i").exec(t)?.[0]).filter(Boolean)];
}

const out = fs.createWriteStream(path.join(outdir, `${stack}.jsonl`));
const browser = await chromium.launch();
let done = 0;
async function worker() {
  const ctxs = {};
  while (jobs.length) {
    const j = jobs.shift();
    const k = `${j.width}-${j.ground}`;
    if (!ctxs[k]) {
      ctxs[k] = await browser.newContext({ viewport: { width: j.width, height: 900 }, colorScheme: j.ground === "navy" ? "dark" : "light" });
      await ctxs[k].addInitScript((g) => { try { localStorage.setItem("ground", g); } catch {} }, j.ground);
    }
    const page = await ctxs[k].newPage();
    const data404 = [], consoleErr = [], pageErr = [];
    page.on("response", (r) => { const u = r.url(); if (u.includes("/data/") && r.status() >= 400) data404.push(`${r.status()} ${u.split("/data/")[1]}`); });
    page.on("console", (m) => { if (m.type() === "error") consoleErr.push(m.text().slice(0, 200)); });
    page.on("pageerror", (e) => pageErr.push(String(e).slice(0, 200)));
    const rec = { stack, ...j };
    try {
      const res = await page.goto(BASE + j.route, { waitUntil: "load", timeout: 60000 });
      rec.status = res?.status();
      await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => { rec.networkidle = false; });
      await page.waitForTimeout(800);
      Object.assign(rec, await page.evaluate(() => {
        const html = document.documentElement.outerHTML;
        const text = document.body.innerText;
        const q = (s) => [...document.querySelectorAll(s)];
        return {
          dataGround: document.documentElement.getAttribute("data-ground"),
          title: document.title,
          textLen: text.length,
          h1: q("h1").map((e) => e.innerText.trim()).slice(0, 3),
          pending: q("[data-pending]").map((e) => `${e.getAttribute("data-pending")}: ${e.innerText.trim()}`),
          marks: q(".mk-marked").map((e) => e.innerText.trim().replace(/\s+/g, " ").slice(0, 160)),
          bareChips: q(".cs-chip[data-placeholder]").filter((e) => !e.closest(".mk-marked")).map((e) => e.innerText.trim()),
          dashes: q(".mk-dash").length,
          placeholderHtml: (html.match(/placeholder\s*(·|&middot;|&#183;)/gi) ?? []).length,
          errorStates: q("section.state[role=alert]").map((e) => e.innerText.trim().replace(/\s+/g, " ").slice(0, 240)),
          awaiting: q(".aw-banner").map((e) => e.innerText.trim().replace(/\s+/g, " ").slice(0, 200)),
          loading: (text.match(/Loading [^\n]{0,60}…/g) ?? []).slice(0, 5),
          stale: q(".banner-stale").map((e) => e.innerText.trim().slice(0, 200)),
          nextError: /Application error|Internal Server Error|This page could not be found|404/.test(document.title + " " + text.slice(0, 400)),
          notFoundText: /could not be found|Not found/i.test(text.slice(0, 600)),
          wide: document.documentElement.scrollWidth - window.innerWidth,
          text,
        };
      }));
      rec.banned = lint(rec.text, j.route.includes("/board"));
      rec.textHead = rec.text.slice(0, 1500);
      if (j.shot) {
        const f = `${j.route.replace(/[/?=.]+/g, "_").replace(/^_/, "") || "root"}-${j.width}-${j.ground}.jpg`;
        await page.screenshot({ path: path.join(outdir, "shots", f), fullPage: true, type: "jpeg", quality: 45 }).catch((e) => { rec.shotErr = String(e).slice(0, 100); });
        rec.shot = f;
      }
      delete rec.text;
    } catch (e) {
      rec.error = String(e).split("\n")[0];
    }
    rec.data404 = data404; rec.consoleErr = consoleErr.slice(0, 8); rec.pageErr = pageErr.slice(0, 8);
    out.write(JSON.stringify(rec) + "\n");
    await page.close();
    if (++done % 25 === 0) console.log(`${stack}: ${done}`);
  }
  for (const c of Object.values(ctxs)) await c.close();
}
await Promise.all([worker(), worker(), worker(), worker()]);
await browser.close();
out.end();
console.log(`${stack}: done ${done}`);
