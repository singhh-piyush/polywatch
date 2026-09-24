// My positions: your open bets and winnings to redeem, what they're worth now, and what tracked traders did on them.

import { state } from "../store.js";
import { get, num } from "../filters.js";
import { ago, esc, oddsRail, secondsToSettle, settles, thumb, usd } from "../util.js";
import { WITHIN } from "./live.js";

export const schema = [
  { type: "search", key: "q", label: "Search", placeholder: "Market or outcome" },
  { type: "seg", key: "status", label: "Status", default: "", options: [
    { value: "", label: "All" }, { value: "open", label: "Open" }, { value: "redeem", label: "Redeem" }] },
  { type: "seg", key: "result", label: "Result", default: "", options: [
    { value: "", label: "Any" }, { value: "win", label: "Winning" }, { value: "lose", label: "Losing" }] },
  { type: "select", key: "within", label: "Market settles", options: WITHIN },
  { type: "toggle", key: "moved", label: "Only ones tracked traders traded today" },
];

const price = (p) => (p.redeemable ? p.cur_price : state.prices[p.asset] ?? p.cur_price);

export function render(root) {
  const f = get("positions");
  const account = state.status.account || {};
  const head = (sub) => `<div class="view-head"><div><h1>My positions</h1><p>${sub}</p></div>
    <div class="view-tools"><button class="btn" data-action="settings">${account.wallet ? "Change account" : "Set your account"}</button></div></div>`;
  if (!account.wallet) {
    root.innerHTML = head("Your open bets, what they're worth now, and when tracked traders sell out of them.") +
      `<div class="empty"><h3>Add your Polymarket account</h3><p>Enter your username or wallet in settings. polywatch only reads your public positions.</p>
      <p style="margin-top:16px"><button class="btn primary" data-action="settings">Set your account</button></p></div>`;
    return;
  }
  const moves = new Map();
  for (const i of state.items.values()) {
    if (!moves.has(i.asset)) moves.set(i.asset, []);
    moves.get(i.asset).push(i);
  }
  const all = state.positions.positions;
  const q = (f.q || "").trim().toLowerCase();
  const within = num(f.within);
  const list = all.filter((p) => {
    const pnl = p.shares * (price(p) - p.avg_price);
    if (f.status === "open" && p.redeemable) return false;
    if (f.status === "redeem" && !p.redeemable) return false;
    if (f.result === "win" && pnl <= 0) return false;
    if (f.result === "lose" && pnl >= 0) return false;
    if (q && !`${p.title} ${p.outcome}`.toLowerCase().includes(q)) return false;
    if (within != null && secondsToSettle(state.markets[p.slug]) > within) return false;
    if (f.moved && !moves.has(p.asset)) return false;
    return true;
  });
  const sort = get("positions").sort || "value";
  list.sort((a, b) => (b.redeemable - a.redeemable) || ({
    value: () => b.shares * price(b) - a.shares * price(a),
    pnl: () => b.shares * (price(b) - b.avg_price) - a.shares * (price(a) - a.avg_price),
    soonest: () => secondsToSettle(state.markets[a.slug]) - secondsToSettle(state.markets[b.slug]),
  }[sort] || (() => 0))());
  const value = all.reduce((s, p) => s + p.shares * price(p), 0);
  const cost = all.reduce((s, p) => s + p.shares * p.avg_price, 0);
  const redeem = all.filter((p) => p.redeemable);
  const totals = `<div class="totals">
    <div class="total"><span>Worth now</span><b>${usd(value)}</b></div>
    <div class="total"><span>Profit / loss</span><b class="${value - cost >= 0 ? "pos" : "neg"}">${usd(value - cost, { sign: true })}</b></div>
    <div class="total"><span>Open</span><b>${all.length - redeem.length}</b></div>
    <div class="total"><span>To redeem</span><b>${redeem.length ? usd(redeem.reduce((s, p) => s + p.shares * p.cur_price, 0)) : "–"}</b></div></div>`;
  const tools = `<div class="view-head" style="margin-bottom:12px"><div class="meta">${list.length} of ${all.length} positions for ${esc(account.name || account.wallet)}</div>
    <div class="view-tools"><select class="select" id="pos-sort" aria-label="Sort">${[["value", "Biggest first"], ["pnl", "Best profit first"], ["soonest", "Settles soonest"]].map(([v, l]) =>
      `<option value="${v}" ${sort === v ? "selected" : ""}>${l}</option>`).join("")}</select></div></div>`;
  const rows = list.map((p) => {
    const now = price(p);
    const pnl = p.shares * (now - p.avg_price);
    const timing = state.markets[p.slug];
    const when = settles(timing);
    const theirs = (moves.get(p.asset) || []).sort((a, b) => b.last_ts - a.last_ts).slice(0, 3);
    return `<article class="bet">
      ${thumb(timing?.icon)}
      <div class="bet-main">
        <div class="bet-title"><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a> · <span class="outcome">${esc(p.outcome)}</span></div>
        <div class="bet-sub">
          <span class="amount">${usd(p.shares * now)}</span>
          <span class="${pnl >= 0 ? "pos" : "neg"}">${usd(pnl, { sign: true })} (${p.avg_price ? `${pnl >= 0 ? "+" : "−"}${Math.abs(pnl / (p.shares * p.avg_price) * 100).toFixed(0)}%` : "–"})</span>
          <span>${p.shares.toFixed(1)} shares</span>
          ${p.redeemable ? `<span class="pill up">Won — redeem on Polymarket</span>` : when ? `<span class="pill" data-settles="${esc(p.slug)}">⏱ ${esc(when)}</span>` : ""}
        </div>
        ${theirs.length ? `<div class="moves">${theirs.map((i) => `<span class="${i.side === "BUY" ? "side-buy" : "side-sell"}">${i.side === "BUY" ? "▲" : "▼"}</span> ${esc(i.name)} ${i.side === "BUY" ? "bought" : "sold"} ${usd(i.usd)} ${ago(i.last_ts)}`).join(" · ")}</div>` : ""}
      </div>
      ${oddsRail(p.avg_price, p.redeemable ? p.cur_price : now, { left: "you paid", right: p.redeemable ? "pays" : "now" })}
      <div></div>
    </article>`;
  }).join("");
  const body = list.length ? `<div class="rows">${rows}</div>` : `<div class="empty"><h3>${all.length ? "No positions match these filters" : "No open positions"}</h3><p>${all.length ? "Loosen a filter on the left." : "Positions you open on Polymarket show up here within 30 seconds."}</p></div>`;
  root.innerHTML = head("Your open bets and winnings to redeem, valued at live prices. Updated every 30 seconds.") + totals + tools + body;
}

export function change(e, rerender) {
  if (e.target.id === "pos-sort") { get("positions").sort = e.target.value; rerender(); }
}
