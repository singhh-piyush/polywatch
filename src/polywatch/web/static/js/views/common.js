// Common trades: outcomes two or more tracked traders bought in the last day.

import { state } from "../store.js";
import { get, num } from "../filters.js";
import { ago, esc, oddsRail, secondsToSettle, settles, thumb, usd } from "../util.js";
import { WITHIN } from "./live.js";

function categories() {
  const seen = new Set(state.common.map((b) => state.markets[b.slug]?.category || "Other"));
  return [...seen].sort().map((c) => ({ value: c, label: c }));
}

export const schema = [
  { type: "search", key: "q", label: "Search", placeholder: "Market, outcome or trader" },
  { type: "seg", key: "min", label: "Traders in it", default: "2", options: [
    { value: "2", label: "2+" }, { value: "3", label: "3+" }, { value: "4", label: "4+" }, { value: "5", label: "5+" }] },
  { type: "toggle", key: "agreed", label: "Hide ones with traders on the other side" },
  { type: "chips", key: "cat", label: "Category", options: categories },
  { type: "select", key: "within", label: "Market settles", options: WITHIN },
  { type: "select", key: "moved", label: "Price moved up since they bought", options: [
    { value: "", label: "Any amount" }, { value: "2", label: "At most 2¢" }, { value: "5", label: "At most 5¢" },
    { value: "10", label: "At most 10¢" }] },
];

export function render(root) {
  const f = get("common");
  const q = (f.q || "").trim().toLowerCase();
  const within = num(f.within), moved = num(f.moved), min = Number(f.min || 2);
  const list = state.common.filter((b) => {
    const timing = state.markets[b.slug];
    const price = state.prices[b.asset];
    if (b.wallets.length < min) return false;
    if (f.agreed && b.against > 0) return false;
    if (q && !`${b.title} ${b.outcome} ${b.names.join(" ")}`.toLowerCase().includes(q)) return false;
    if (f.cat?.length && !f.cat.includes(timing?.category || "Other")) return false;
    if (within != null && secondsToSettle(timing) > within) return false;
    if (moved != null && price != null && (price - b.avg_price) * 100 > moved) return false;
    return true;
  });
  const head = `<div class="view-head"><div><h1>Common trades</h1>
    <p>Outcomes that several of your traders bought in the last 24 hours. The more of them agree, the higher it sits.</p></div>
    <div class="view-tools"><span class="meta">${list.length} of ${state.common.length}</span></div></div>`;
  if (!list.length) {
    root.innerHTML = head + `<div class="empty"><h3>${state.common.length ? "Nothing matches these filters" : "No common trades yet"}</h3>
      <p>${state.common.length ? "Loosen a filter on the left." : "When two tracked traders buy the same outcome within a day, it shows up here."}</p></div>`;
    return;
  }
  root.innerHTML = head + `<div class="cards">${list.map((b) => {
    const timing = state.markets[b.slug];
    const when = settles(timing);
    return `<article class="card">
      <div class="card-head">${thumb(timing?.icon)}<div class="bet-main">
        <div class="bet-title"><a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.title)}</a> · <span class="outcome">${esc(b.outcome)}</span></div>
        <div class="bet-sub"><span>last buy ${ago(b.last_ts)}</span>${when ? `<span class="pill" data-settles="${esc(b.slug)}">⏱ ${esc(when)}</span>` : ""}
        ${timing?.category ? `<span class="pill">${esc(timing.category)}</span>` : ""}</div></div></div>
      <div class="kv"><span><b>${b.wallets.length} traders</b> bought ${usd(b.usd)}</span>
        ${b.against ? `<span class="neg">${b.against} bet the other way</span>` : `<span class="pos">nobody against</span>`}</div>
      <div class="names">${b.wallets.map((w, n) => `<button class="pill who" data-wallet="${esc(w)}">${esc(b.names[n])}</button>`).join("")}</div>
      ${oddsRail(b.avg_price, state.prices[b.asset], { left: "they paid" })}
    </article>`;
  }).join("")}</div>`;
}
