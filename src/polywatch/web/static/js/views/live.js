// Live bets: tracked traders' buys (or sells), best to copy first, with filters for price, size, timing and more.

import { state } from "../store.js";
import { get, num } from "../filters.js";
import { ago, cents, esc, oddsRail, scoreRing, secondsToSettle, settles, thumb, usd } from "../util.js";

const PAGE = 60;
let shown = PAGE;
let frozenOrder = null; // keys in the order shown while the pointer rests on the list

const WITHIN = [
  { value: "", label: "Any time" }, { value: "3600", label: "Within 1 hour" }, { value: "21600", label: "Within 6 hours" },
  { value: "86400", label: "Within 24 hours" }, { value: "259200", label: "Within 3 days" },
  { value: "604800", label: "Within 7 days" }, { value: "2592000", label: "Within 30 days" },
];
export { WITHIN };

export function categories() {
  const seen = new Map();
  for (const i of state.items.values()) {
    const c = state.markets[i.slug]?.category || "Other";
    seen.set(c, (seen.get(c) || 0) + 1);
  }
  return [...seen.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => ({ value: c, label: c }));
}

export const schema = [
  { type: "search", key: "q", label: "Search", placeholder: "Market, outcome or trader" },
  { type: "seg", key: "side", label: "Show", default: "BUY", options: [{ value: "BUY", label: "Buys" }, { value: "SELL", label: "Sells" }] },
  { type: "chips", key: "cat", label: "Category", options: categories },
  { type: "slider", key: "minScore", label: "Copy score at least", min: 0, max: 100, step: 5, default: 0, hint: "Buys only. Trader quality, bet size, agreement, age and price move combined." },
  { type: "pair", key: "price", label: "Price paid (¢)", placeholders: ["0", "100"], hint: "The price is also the chance the market gives it." },
  { type: "pair", key: "usd", label: "Bet size ($)", placeholders: ["100", "any"] },
  { type: "select", key: "within", label: "Market settles", options: WITHIN },
  { type: "select", key: "age", label: "Placed", options: [
    { value: "", label: "Last 24 hours" }, { value: "900", label: "Last 15 minutes" }, { value: "3600", label: "Last hour" },
    { value: "21600", label: "Last 6 hours" }] },
  { type: "toggle", key: "hideDim", label: "Hide bets not worth copying", default: true, hint: "95¢ or more, or one side of a trade that bought both outcomes." },
  { type: "toggle", key: "conviction", label: "Only unusually big bets 🔥", hint: "At least 3x the trader's usual size." },
  { type: "toggle", key: "mine", label: "Only markets I hold" },
  { type: "toggle", key: "pinned", label: "Only pinned traders" },
  { type: "slider", key: "minTrader", label: "Trader score at least", min: 0, max: 100, step: 5, default: 0 },
  { type: "slider", key: "minWin", label: "Trader win rate at least", min: 0, max: 100, step: 5, default: 0, format: (x) => `${x}%` },
  { type: "checklist", key: "who", label: "Traders", options: () => Object.values(state.watched)
      .sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9))
      .map((w) => ({ value: w.wallet, label: w.name, sub: w.rank ? `#${w.rank}` : "pinned" })) },
];

function filtered() {
  const f = get("live");
  const t = Date.now() / 1000;
  const q = (f.q || "").trim().toLowerCase();
  const [pMin, pMax, uMin, uMax] = [num(f.priceMin), num(f.priceMax), num(f.usdMin), num(f.usdMax)];
  const within = num(f.within), age = num(f.age);
  const held = state.positions.holdings || {};
  const out = [];
  for (const i of state.items.values()) {
    if (i.side !== (f.side || "BUY")) continue;
    const w = state.watched[i.wallet];
    const timing = state.markets[i.slug];
    if (q && !`${i.title} ${i.outcome} ${i.name}`.toLowerCase().includes(q)) continue;
    if (f.cat?.length && !f.cat.includes(timing?.category || "Other")) continue;
    if (f.side !== "SELL" && f.minScore && (state.scores[i.key] ?? 0) < f.minScore) continue;
    if (pMin != null && i.avg_price * 100 < pMin) continue;
    if (pMax != null && i.avg_price * 100 > pMax) continue;
    if (uMin != null && i.usd < uMin) continue;
    if (uMax != null && i.usd > uMax) continue;
    if (within != null && secondsToSettle(timing, t) > within) continue;
    if (age != null && t - i.last_ts > age) continue;
    if (f.hideDim && i.fast) continue;
    if (f.conviction && !(i.conviction >= (state.settings.conviction_multiple || 3))) continue;
    if (f.mine && !held[i.asset]) continue;
    if (f.pinned && !w?.pinned) continue;
    if (f.minTrader && (w?.score ?? 0) < f.minTrader) continue;
    if (f.minWin && (w?.win_rate ?? 0) * 100 < f.minWin) continue;
    if (f.who?.length && !f.who.includes(i.wallet)) continue;
    out.push(i);
  }
  return out;
}

const SORTS = {
  best: (a, b) => (state.scores[b.key] ?? -1) - (state.scores[a.key] ?? -1) || b.last_ts - a.last_ts,
  newest: (a, b) => b.last_ts - a.last_ts,
  biggest: (a, b) => b.usd - a.usd,
  soonest: (a, b) => secondsToSettle(state.markets[a.slug]) - secondsToSettle(state.markets[b.slug]) || b.last_ts - a.last_ts,
};

function savedSort() {
  try { return localStorage.getItem("pw-sort") || "best"; } catch { return "best"; }
}

function row(i) {
  const w = state.watched[i.wallet] || {};
  const timing = state.markets[i.slug];
  const price = state.prices[i.asset];
  const buy = i.side === "BUY";
  const holding = state.positions.holdings?.[i.asset];
  const big = i.conviction >= (state.settings.conviction_multiple || 3);
  const fresh = state.fresh.has(i.key);
  const when = settles(timing);
  return `<article class="bet${i.fast ? " dim" : ""}${fresh ? " flash" : ""}" data-key="${esc(i.key)}">
    ${thumb(timing?.icon)}
    <div class="bet-main">
      <div class="bet-title"><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a> · <span class="outcome">${esc(i.outcome)}</span></div>
      <div class="bet-sub">
        <span class="${buy ? "side-buy" : "side-sell"}">${buy ? "Bought" : "Sold"}</span>
        <span class="amount">${usd(i.usd)}</span>
        ${big && buy ? `<span class="fire" title="${i.conviction.toFixed(1)}x their usual bet">🔥 ${i.conviction.toFixed(1)}x usual</span>` : ""}
        <span>by <button class="who" data-wallet="${esc(i.wallet)}">${esc(w.name || i.name)}</button>${w.rank ? ` #${w.rank}` : ""}${w.win_rate != null ? ` · ${Math.round(w.win_rate * 100)}% win` : ""}${w.pinned ? " · ★" : ""}</span>
        <span title="${new Date(i.last_ts * 1000).toLocaleString()}" data-ago="${i.last_ts}">${ago(i.last_ts)}</span>
        ${when ? `<span class="pill" data-settles="${esc(i.slug)}">⏱ ${esc(when)}</span>` : ""}
        ${timing?.category ? `<span class="pill">${esc(timing.category)}</span>` : ""}
        ${i.fast ? `<span class="pill warn" title="Nothing to gain by copying">${esc(i.fast)}</span>` : ""}
        ${holding ? `<span class="pill iris" title="You hold ${holding.shares.toFixed(1)} shares at ${cents(holding.avg_price)}">You hold this</span>` : ""}
      </div>
    </div>
    ${oddsRail(i.avg_price, price, { left: buy ? "paid" : "sold at" })}
    ${buy ? scoreRing(state.scores[i.key]) : "<div></div>"}
  </article>`;
}

export function render(root) {
  const f = get("live");
  const sort = f.side === "SELL" ? "newest" : savedSort();
  let list = filtered();
  const hovering = root.querySelector(".rows:hover");
  if (hovering && frozenOrder) {
    // Keep rows where they are while the pointer is on them; new ones go on top.
    const pos = new Map(frozenOrder.map((k, n) => [k, n]));
    list.sort((a, b) => (pos.get(a.key) ?? -1) - (pos.get(b.key) ?? -1) || b.last_ts - a.last_ts);
  } else {
    list.sort(SORTS[sort] || SORTS.best);
  }
  frozenOrder = list.map((i) => i.key);
  const total = state.items.size;
  const acct = state.status.account?.wallet;
  const head = `<div class="view-head">
    <div><h1>${f.side === "SELL" ? "Sells" : "Live bets"}</h1>
    <p>${f.side === "SELL"
      ? `Tracked traders selling. ${acct ? "Turn on “Only markets I hold” to see exits from your positions." : ""}`
      : "What the traders you follow are buying, with the ones most worth copying first. Scores update every 10 seconds."}</p></div>
    <div class="view-tools">
      ${hovering ? `<span class="paused">Order paused while you browse</span>` : ""}
      <span class="meta">${list.length} of ${total}</span>
      ${f.side === "SELL" ? "" : `<select class="select" id="live-sort" aria-label="Sort">
        ${[["best", "Best to copy"], ["newest", "Newest"], ["biggest", "Biggest"], ["soonest", "Settles soonest"]].map(([v, l]) =>
          `<option value="${v}" ${sort === v ? "selected" : ""}>${l}</option>`).join("")}</select>`}
    </div></div>`;
  let body;
  if (!list.length) {
    body = total
      ? `<div class="empty"><h3>No bets match these filters</h3><p>Loosen a filter on the left, or press Reset.</p></div>`
      : `<div class="empty"><h3>Waiting for bets</h3><p>${Object.keys(state.watched).length
          ? "Bets from the traders you follow show up here as they happen. The last 24 hours load first."
          : "No traders are tracked yet. A scan is ranking traders now; bets start once it finishes."}</p></div>`;
  } else {
    body = `<div class="rows">${list.slice(0, shown).map(row).join("")}</div>` +
      (list.length > shown ? `<div class="more"><button class="btn" id="live-more">Show ${Math.min(PAGE, list.length - shown)} more</button></div>` : "");
  }
  root.innerHTML = head + body;
  for (const k of frozenOrder.slice(0, shown)) state.fresh.delete(k);
}

export function change(e, rerender) {
  if (e.target.id === "live-sort") {
    try { localStorage.setItem("pw-sort", e.target.value); } catch { /* private window */ }
    rerender();
  }
}

export function click(e, rerender) {
  if (e.target.id === "live-more") { shown += PAGE; rerender(); }
}

export function resetPaging() { shown = PAGE; }
