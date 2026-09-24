// Entry point: tabs, rendering, live countdowns, the drawer (trader details and settings), alerts and toasts.

import { connect, on, onChange, state } from "./store.js";
import * as filters from "./filters.js";
import { ago, cents, clock, displayName, esc, post, settles, shortWallet, usd, usdShort, pct } from "./util.js";
import * as live from "./views/live.js";
import * as common from "./views/common.js";
import * as traders from "./views/traders.js";
import * as positions from "./views/positions.js";
import * as short from "./views/short.js";

const VIEWS = { live, common, traders, positions, short };
// Which state changes each view cares about; anything else doesn't trigger a re-render.
const DEPENDS = {
  live: ["items", "scores", "prices", "markets", "traders", "positions"],
  common: ["common", "prices", "markets"],
  traders: ["traders", "status"],
  positions: ["positions", "prices", "markets", "items", "status"],
  short: ["short", "short_index", "short_board"],
};

const view = document.getElementById("view");
let tab = "live";
let lastRender = 0;
let renderTimer = null;

function pref(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function savePref(key, value) { try { localStorage.setItem(key, value); } catch { /* storage blocked */ } }

// --- routing --------------------------------------------------------------------------------------
function route() {
  const [name, query] = location.hash.slice(1).split("?");
  tab = VIEWS[name] ? name : "live";
  for (const a of document.querySelectorAll(".tabs a")) a.setAttribute("aria-selected", String(a.dataset.tab === tab));
  filters.mount(tab, VIEWS[tab].schema, query ?? null, onFilters);
  live.resetPaging();
  render(true);
  view.scrollTop = 0;
}

function onFilters() {
  if (tab === "short") short.filtersChanged();
  render(true);
}

// Re-render the current view, at most four times a second.
function render(now = false) {
  if (!state.ready) return;
  const due = 250 - (performance.now() - lastRender);
  if (!now && due > 0) {
    if (!renderTimer) renderTimer = setTimeout(() => { renderTimer = null; render(true); }, due);
    return;
  }
  lastRender = performance.now();
  const scroll = window.scrollY;
  VIEWS[tab].render(view, () => render());
  window.scrollTo(0, scroll);
  filters.refreshOptions();
  updateCounts();
}

function updateCounts() {
  const buys = [...state.items.values()].filter((i) => i.side === "BUY" && !i.fast).length;
  document.querySelector('[data-count="live"]').textContent = buys || "";
  document.querySelector('[data-count="common"]').textContent = state.common.length || "";
  document.querySelector('[data-count="positions"]').textContent = state.positions.positions.length || "";
}

onChange((parts) => {
  renderStatus();
  if (parts.has("short_board")) short.boardChanged();
  if (parts.has("all") || DEPENDS[tab].some((p) => parts.has(p))) render();
  if (openDrawer === "settings" && parts.has("status")) drawSettings(false);
});

// --- status bar -----------------------------------------------------------------------------------
function renderStatus() {
  const s = state.status;
  const liveTrades = state.connected && s.stream === "live";
  const dot = !state.connected ? "" : liveTrades ? "live" : "wait";
  const account = s.account?.wallet ? `<span class="extra">${esc(s.account.name || shortWallet(s.account.wallet))}</span>` : "";
  const scan = s.scan ? `<span class="extra">Scanning ${s.scan.done}/${s.scan.total || "…"}</span>` : "";
  document.getElementById("status").innerHTML = `
    <span title="${liveTrades ? "Receiving every Polymarket trade as it happens" : "Connecting to the live trade stream"}"><span class="dot ${dot}"></span>${!state.connected ? "Offline" : liveTrades ? "Live" : "Connecting"}</span>
    <span class="extra" title="Prices ${s.prices === "live" ? "stream in live" : "are polled every 30s while the live price feed reconnects"}">${s.rates ? `${s.rates.trades}/s trades · ${s.rates.requests}/s requests` : ""}</span>
    ${scan}${account}`;
}

// --- live clocks: countdowns, "x ago" and settle times, every second without a re-render --------------
setInterval(() => {
  const t = Date.now() / 1000;
  for (const el of view.querySelectorAll("[data-ends]")) {
    const left = Number(el.dataset.ends) - t;
    if (left <= 0) continue;
    el.firstChild.textContent = clock(left);
    el.classList.toggle("soon", left < 60);
  }
  for (const el of view.querySelectorAll("[data-ago]")) el.textContent = ago(Number(el.dataset.ago));
  for (const el of view.querySelectorAll("[data-settles]")) {
    const text = settles(state.markets[el.dataset.settles], t);
    if (text) el.textContent = `⏱ ${text}`;
  }
}, 1000);

// --- drawer ---------------------------------------------------------------------------------------
const drawer = document.getElementById("drawer");
const drawerBody = document.getElementById("drawer-body");
let openDrawer = null;
let lastFocus = null;

function showDrawer(kind, html) {
  openDrawer = kind;
  drawerBody.innerHTML = html;
  if (!drawer.classList.contains("open")) {
    lastFocus = document.activeElement;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    drawer.querySelector(".drawer-close").focus();
  }
}

function closeDrawer() {
  openDrawer = null;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  lastFocus?.focus?.();
}

drawer.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDrawer(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (drawer.classList.contains("open")) closeDrawer();
    document.getElementById("filters").classList.remove("open");
  }
});

function stat(label, value) { return `<div class="stat"><span>${label}</span><b>${value}</b></div>`; }

async function drawTrader(wallet) {
  const t = state.traders.find((x) => x.wallet === wallet);
  const w = state.watched[wallet];
  const name = t?.name || w?.name || shortWallet(wallet);
  const mode = t?.mode || (w?.pinned ? "pin" : null);
  const days = state.settings.window_days || 90;
  const body = t && t.n ? `<div class="stats">
      ${stat("Rank", t.rank ? `#${t.rank}` : "–")}${stat("Score", t.rank ? Math.round(t.score) : "–")}
      ${stat("Win rate", pct(t.win_rate))}${stat("Avg price paid", cents(t.mean_price))}
      ${stat("Beats the odds by", `${(t.edge * 100).toFixed(1)} pts`)}${stat("Return", pct(t.roi))}
      ${stat(`Profit (${days}d)`, `<span class="${t.pnl >= 0 ? "pos" : "neg"}">${usdShort(t.pnl, { sign: true })}</span>`)}${stat("Resolved bets", `${t.n} (${t.wins} won)`)}
      ${stat("Typical bet", usd(t.median_bet))}${stat("Too fast to copy", t.fast_share == null ? "–" : pct(t.fast_share))}
      ${stat("Account age", t.account_age_d == null ? "–" : `${Math.round(t.account_age_d)} days`)}${stat("Last trade", t.last_trade_ts ? ago(t.last_trade_ts) : "–")}
    </div>` : `<p class="meta">No stats yet. They arrive with the next scan.</p>`;
  const why = t?.excluded ? `<h3>Why they're left out</h3><p class="meta">${esc(t.excluded)}</p>` : "";
  const flags = t?.flags?.length ? `<h3>Warning flags</h3><div class="flag-list">${t.flags.map((f) => `<div><b>${esc(f)}</b>${esc(t.flag_help[f])}</div>`).join("")}</div>` : "";
  const bets = [...state.items.values()].filter((i) => i.wallet === wallet).sort((a, b) => b.last_ts - a.last_ts).slice(0, 8);
  const recent = bets.length ? `<h3>Bets today</h3><div class="pos-list">${bets.map((i) => `<div class="pos-item"><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a> · <span class="outcome">${esc(i.outcome)}</span>
      <div class="sub"><span class="${i.side === "BUY" ? "side-buy" : "side-sell"}">${i.side === "BUY" ? "Bought" : "Sold"}</span> ${usd(i.usd)} at ${cents(i.avg_price)} · ${ago(i.last_ts)}</div></div>`).join("")}</div>` : "";
  showDrawer("trader", `<h2 id="drawer-title">${esc(name)}</h2><div class="addr">${esc(wallet)}</div>
    <div class="actions">
      <button class="btn ${mode === "pin" ? "" : "primary"}" data-override="${mode === "pin" ? "" : "pin"}" data-wallet="${esc(wallet)}">${mode === "pin" ? "Unpin" : "Pin — always follow"}</button>
      <button class="btn danger" data-override="${mode === "ban" ? "" : "ban"}" data-wallet="${esc(wallet)}">${mode === "ban" ? "Unban" : "Ban — never follow"}</button>
      <a class="btn" href="https://polymarket.com/profile/${esc(wallet)}" target="_blank" rel="noopener" style="text-decoration:none">Open profile ↗</a>
    </div>
    <h3>Last ${days} days</h3>${body}${why}${flags}${recent}<h3>Open positions</h3><div id="trader-positions" class="meta">Loading…</div>`);
  try {
    const res = await fetch(`/api/trader/${wallet}/positions`);
    const data = await res.json();
    const el = document.getElementById("trader-positions");
    if (!el) return;
    el.outerHTML = data.positions?.length ? `<div class="pos-list">${data.positions.slice(0, 12).map((p) => `<div class="pos-item">${esc(p.title)} · <span class="outcome">${esc(p.outcome)}</span>
      <div class="sub">${cents(p.avgPrice)} → ${cents(p.curPrice)} · worth ${usd(p.currentValue)} · <span class="${p.cashPnl >= 0 ? "pos" : "neg"}">${usd(p.cashPnl, { sign: true })}</span></div></div>`).join("")}</div>`
      : `<p class="meta">${data.error ? "Couldn't load their positions." : "None right now."}</p>`;
  } catch { /* the drawer may have closed */ }
}

function drawShortTrader(wallet) {
  const r = short.shortTrader(wallet);
  const name = displayName(r?.name, wallet);
  const body = r ? `<div class="stats">
      ${stat("Profit", `<span class="${r.pnl >= 0 ? "pos" : "neg"}">${usdShort(r.pnl, { sign: true })}</span>`)}${stat("Consistency", `${Math.round(r.consistency)} / 100`)}
      ${stat("Win rate", pct(r.win_rate))}${stat("Avg price paid", cents(r.avg_price))}
      ${stat("Windows", r.windows.toLocaleString())}${stat("Return", pct(r.roi, 1))}
      ${stat("Volume", usdShort(r.volume))}${stat("Typical bet", usd(r.median_bet))}</div>
      ${r.badges.length ? `<h3>Badges</h3><div class="flag-list">${r.badges.map((b) => `<div><b>${b}</b>${esc({ ARB: "Often buys Up and Down in the same window.", SNIPE: "Most of its money goes in at 95¢ or more.", "LAST-SEC": "Usually still buying in the last seconds.", HFT: "Dozens of fills per window.", MAKER: "Often sells pairs it minted, like a market maker." }[b] || "")}</div>`).join("")}</div>` : ""}`
    : `<p class="meta">Not on the current leaderboard.</p>`;
  showDrawer("short", `<h2 id="drawer-title">${esc(name)}</h2><div class="addr">${esc(wallet)}</div>
    <div class="actions">
      <button class="btn ${r?.star ? "" : "primary"}" data-star="${esc(wallet)}">${r?.star ? "Unstar" : "Star — always follow live"}</button>
      <button class="btn" data-bell="${esc(wallet)}">${r?.bell ? "Turn alerts off" : "Alert me when they bet"}</button>
      <a class="btn" href="https://polymarket.com/profile/${esc(wallet)}" target="_blank" rel="noopener" style="text-decoration:none">Open profile ↗</a>
    </div><h3>Short markets</h3>${body}`);
}

function notificationsOn() { return pref("pw-notify", "0") === "1" && "Notification" in window && Notification.permission === "granted"; }

function drawSettings(fresh = true) {
  const s = state.status;
  const acct = s.account || {};
  const active = document.activeElement?.id;
  const typed = document.getElementById("acct-input")?.value;
  const html = `<h2 id="drawer-title">Settings</h2>
    <h3>Your Polymarket account</h3>
    <p class="meta" style="margin:0 0 10px">${acct.wallet ? `Following <b>${esc(acct.name || shortWallet(acct.wallet))}</b>. Your positions and exits show up in My positions.` : "Add your account to see your positions and get alerts when tracked traders sell what you hold."}</p>
    <form class="field" id="acct-form"><label for="acct-input">Username, profile link or wallet</label>
      <div class="row"><input class="input" id="acct-input" autocomplete="off" placeholder="@name or 0x…" value="${esc(typed ?? "")}"><button class="btn primary">Find</button></div>
      <div class="matches" id="acct-matches"></div></form>
    ${acct.wallet ? `<button class="btn danger" data-action="clear-account">Remove account</button>` : ""}
    <h3>Traders</h3>
    <form class="field" id="add-form"><label for="add-input">Pin a trader by username, profile link or wallet</label>
      <div class="row"><input class="input" id="add-input" autocomplete="off" placeholder="@name or 0x…"><button class="btn">Find</button></div>
      <div class="matches" id="add-matches"></div></form>
    <div class="settings-row" id="scan-row">${scanRow(s)}</div>
    <h3>Alerts</h3>
    <div class="settings-row"><div>Desktop alerts<p>System notifications for big bets, pinned traders and exits from your positions.</p></div>
      <label class="toggle"><input type="checkbox" id="alerts-toggle" ${s.alerts ? "checked" : ""}></label></div>
    <div class="settings-row"><div>Browser notifications<p>The same alerts from this tab, even when it's in the background.</p></div>
      <label class="toggle"><input type="checkbox" id="notify-toggle" ${notificationsOn() ? "checked" : ""}></label></div>`;
  if (fresh || openDrawer !== "settings") showDrawer("settings", html);
  else {
    const row = document.getElementById("scan-row");
    if (row) row.innerHTML = scanRow(s);
  }
  if (active && document.getElementById(active)) document.getElementById(active).focus();
}

function scanRow(s) {
  return `<div>Rescan traders<p>${s.scan ? `Scanning ${s.scan.done} of ${s.scan.total || "…"}.` : s.scan_ts ? `Last scan ${ago(s.scan_ts)}.` : "No scan yet."} A scan takes a few minutes.</p></div>
      <button class="btn" data-action="rescan" ${s.scan ? "disabled" : ""}>${s.scan ? "Scanning…" : "Rescan"}</button>`;
}

async function findTrader(form, input, list, onPick) {
  list.innerHTML = `<p class="meta">Searching…</p>`;
  try {
    const { matches } = await post("/api/resolve", { text: input.value });
    if (!matches.length) { list.innerHTML = `<p class="meta">No trader named “${esc(input.value)}”. Check the spelling or paste their wallet address.</p>`; return; }
    if (matches.length === 1 && !matches[0].name) { onPick(matches[0]); return; }
    list.innerHTML = matches.map((m, n) => `<button type="button" class="match" data-pick="${n}"><span>${esc(m.name || "(no name)")}</span><span class="addr">${shortWallet(m.wallet)}</span></button>`).join("");
    list.onclick = (e) => { const b = e.target.closest("[data-pick]"); if (b) onPick(matches[Number(b.dataset.pick)]); };
  } catch (err) {
    list.innerHTML = `<p class="meta">${esc(err.message)}</p>`;
  }
}

drawerBody.addEventListener("submit", (e) => {
  e.preventDefault();
  if (e.target.id === "acct-form") {
    findTrader(e.target, document.getElementById("acct-input"), document.getElementById("acct-matches"), async (m) => {
      await post("/api/account", { wallet: m.wallet, name: m.name });
      toast({ text: `Following ${m.name || shortWallet(m.wallet)}'s positions` });
      document.getElementById("acct-input").value = "";
      drawSettings();
    });
  } else if (e.target.id === "add-form") {
    findTrader(e.target, document.getElementById("add-input"), document.getElementById("add-matches"), async (m) => {
      await post("/api/override", { wallet: m.wallet, mode: "pin", name: m.name });
      toast({ text: `Pinned ${m.name || shortWallet(m.wallet)}. Full stats arrive with the next scan.` });
      document.getElementById("add-input").value = "";
      document.getElementById("add-matches").innerHTML = "";
    });
  }
});

drawerBody.addEventListener("change", async (e) => {
  if (e.target.id === "alerts-toggle") await post("/api/alerts", { enabled: e.target.checked });
  if (e.target.id === "notify-toggle") {
    if (e.target.checked && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission();
      if (p !== "granted") { e.target.checked = false; toast({ text: "Notifications are blocked for this site in your browser settings.", kind: "error" }); return; }
    }
    savePref("pw-notify", e.target.checked ? "1" : "0");
  }
});

// --- clicks anywhere ------------------------------------------------------------------------------
document.addEventListener("click", async (e) => {
  const t = e.target;
  const action = t.closest("[data-action]")?.dataset.action;
  if (action === "settings" || t.closest("#open-settings")) { drawSettings(); return; }
  if (action === "add-trader") { drawSettings(); setTimeout(() => document.getElementById("add-input")?.focus(), 50); return; }
  if (action === "rescan") {
    const { started } = await post("/api/rescan", {});
    toast({ text: started ? "Scanning traders. The first scan takes a few minutes." : "A scan is already running." });
    return;
  }
  if (action === "clear-account") { await post("/api/account", { wallet: null }); drawSettings(); return; }
  const override = t.closest("[data-override]");
  if (override) {
    const mode = override.dataset.override || null;
    await post("/api/override", { wallet: override.dataset.wallet, mode });
    toast({ text: mode === "pin" ? "Pinned. You'll see all their bets." : mode === "ban" ? "Banned. Their bets are hidden." : "Removed." });
    setTimeout(() => drawTrader(override.dataset.wallet), 300);
    return;
  }
  if (drawer.contains(t) && t.closest("[data-star],[data-bell]")) {
    const b = t.closest("[data-star],[data-bell]");
    const wallet = b.dataset.star || b.dataset.bell;
    const r = short.shortTrader(wallet);
    const body = b.dataset.star ? { wallet, star: !r?.star } : { wallet, bell: !r?.bell };
    await post("/api/short/pref", body);
    if (r) Object.assign(r, body);
    drawShortTrader(wallet);
    render(true);
    return;
  }
  if (!view.contains(t)) return;
  if (VIEWS[tab].click?.(e, () => render(true))) return;
  const who = t.closest("[data-wallet]");
  if (who && !t.closest("a")) { drawTrader(who.dataset.wallet); return; }
  const shortWho = t.closest("[data-short-wallet]");
  if (shortWho && !t.closest("a, button.star-btn")) drawShortTrader(shortWho.dataset.shortWallet);
});

view.addEventListener("change", (e) => VIEWS[tab].change?.(e, () => render(true)));
view.addEventListener("mouseleave", () => render(true));

document.getElementById("reset-filters").addEventListener("click", () => filters.reset());
document.getElementById("open-filters").addEventListener("click", () => document.getElementById("filters").classList.add("open"));
document.getElementById("close-filters").addEventListener("click", () => document.getElementById("filters").classList.remove("open"));

// --- toasts and alerts ----------------------------------------------------------------------------
function toast({ text, title, url, kind }) {
  const el = document.createElement("div");
  el.className = `toast${kind === "error" ? " error" : ""}`;
  el.innerHTML = `${title ? `<b>${esc(title)}</b>` : ""}${esc(text)}${url ? ` <a href="${esc(url)}" target="_blank" rel="noopener">Open market ↗</a>` : ""}`;
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), url ? 12000 : 5000);
}

on("toast", toast);
on("alert", (a) => {
  toast({ title: a.title, text: a.body, url: a.url });
  if (notificationsOn() && document.hidden) {
    const n = new Notification(a.title, { body: a.body, tag: a.key || a.title });
    n.onclick = () => { window.open(a.url, "_blank"); n.close(); };
  }
});

// --- start ----------------------------------------------------------------------------------------
filters.bind();
window.addEventListener("hashchange", route);
connect().then(() => { route(); renderStatus(); }).catch(() => {
  view.innerHTML = `<div class="empty"><h3>Can't reach polywatch</h3><p>Start it with <b>polywatch web</b> and reload this page.</p></div>`;
});
