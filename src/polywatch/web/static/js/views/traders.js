// Traders: every scanned trader, ranked, with filters on the stats that decide who is worth following.

import { state } from "../store.js";
import { get } from "../filters.js";
import { esc, pct, usdShort } from "../util.js";

export const schema = [
  { type: "search", key: "q", label: "Search", placeholder: "Name or wallet" },
  { type: "seg", key: "show", label: "Show", default: "ranked", options: [
    { value: "watched", label: "Followed" }, { value: "ranked", label: "Ranked" }, { value: "all", label: "All" }],
    hint: "Followed: the traders whose bets you see. All includes the ones left out (bots, one-hit wonders…)." },
  { type: "seg", key: "mode", label: "Your picks", default: "", options: [
    { value: "", label: "Any" }, { value: "pin", label: "Pinned" }, { value: "ban", label: "Banned" }] },
  { type: "seg", key: "flags", label: "Warning flags", default: "", options: [
    { value: "", label: "Any" }, { value: "none", label: "None" }, { value: "only", label: "Flagged" }] },
  { type: "slider", key: "minScore", label: "Score at least", min: 0, max: 100, step: 5, default: 0 },
  { type: "slider", key: "minWin", label: "Win rate at least", min: 0, max: 100, step: 5, default: 0, format: (x) => `${x}%` },
  { type: "slider", key: "minEdge", label: "Beats the odds by at least", min: 0, max: 30, step: 1, default: 0, format: (x) => `${x} pts`,
    hint: "Win rate minus the average price paid." },
  { type: "slider", key: "minRoi", label: "Return at least", min: 0, max: 100, step: 5, default: 0, format: (x) => `${x}%` },
  { type: "slider", key: "minBets", label: "Resolved bets at least", min: 0, max: 500, step: 10, default: 0 },
];

const COLUMNS = [
  { key: "rank", label: "#", num: true, get: (t) => t.rank || 1e9 },
  { key: "name", label: "Trader", get: (t) => t.name.toLowerCase() },
  { key: "score", label: "Score", num: true, get: (t) => t.score },
  { key: "win_rate", label: "Win rate", num: true, get: (t) => t.win_rate },
  { key: "edge", label: "Beats odds", num: true, get: (t) => t.edge },
  { key: "roi", label: "Return", num: true, get: (t) => t.roi },
  { key: "pnl", label: "Profit", num: true, get: (t) => t.pnl },
  { key: "n", label: "Bets", num: true, get: (t) => t.n },
  { key: "median_bet", label: "Typical bet", num: true, get: (t) => t.median_bet },
];

let sortKey = "rank", sortDir = 1;

export function render(root) {
  const f = get("traders");
  const q = (f.q || "").trim().toLowerCase();
  let list = state.traders.filter((t) => {
    if (f.show === "watched" && !t.watched) return false;
    if ((f.show || "ranked") === "ranked" && !t.rank && t.mode !== "pin") return false;
    if (f.mode && t.mode !== f.mode) return false;
    if (f.flags === "none" && t.flags.length) return false;
    if (f.flags === "only" && !t.flags.length) return false;
    if (q && !`${t.name} ${t.wallet}`.toLowerCase().includes(q)) return false;
    if (f.minScore && t.score < f.minScore) return false;
    if (f.minWin && t.win_rate * 100 < f.minWin) return false;
    if (f.minEdge && t.edge * 100 < f.minEdge) return false;
    if (f.minRoi && t.roi * 100 < f.minRoi) return false;
    if (f.minBets && t.n < f.minBets) return false;
    return true;
  });
  const col = COLUMNS.find((c) => c.key === sortKey) || COLUMNS[0];
  list.sort((a, b) => {
    const x = col.get(a), y = col.get(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
  });
  const scan = state.status.scan;
  const head = `<div class="view-head"><div><h1>Traders</h1>
    <p>Ranked by how reliably they beat the odds over the last ${state.settings.window_days || 90} days. Click a trader for details, to pin them or to ban them.</p></div>
    <div class="view-tools"><span class="meta">${list.length} shown</span>
      <button class="btn" data-action="add-trader">Add a trader</button>
      <button class="btn" data-action="rescan" ${scan ? "disabled" : ""}>${scan ? `Scanning ${scan.done}/${scan.total || "…"}` : "Rescan"}</button></div></div>`;
  if (!list.length) {
    root.innerHTML = head + `<div class="empty"><h3>${state.traders.length ? "No traders match these filters" : "No scan yet"}</h3>
      <p>${state.traders.length ? "Loosen a filter on the left." : "The first scan takes a few minutes. It starts on its own."}</p></div>`;
    return;
  }
  const th = COLUMNS.map((c) => `<th class="sortable${c.num ? " num" : ""}${c.key === sortKey ? " sorted" : ""}" data-sort="${c.key}">${c.label}${c.key === sortKey ? (sortDir > 0 ? " ↑" : " ↓") : ""}</th>`).join("");
  const rows = list.slice(0, 400).map((t) => `<tr data-wallet="${esc(t.wallet)}">
    <td class="rank">${t.rank || "–"}</td>
    <td><div class="name-cell"><span class="n">${esc(t.name)}</span>
      ${t.mode === "pin" ? `<span class="pill iris">pinned</span>` : ""}${t.mode === "ban" ? `<span class="pill down">banned</span>` : ""}
      ${t.watched ? "" : t.excluded ? `<span class="pill" title="${esc(t.excluded)}">left out</span>` : ""}
      ${t.flags.map((fl) => `<span class="pill warn" title="${esc(t.flag_help[fl] || fl)}">${esc(fl)}</span>`).join("")}</div></td>
    <td class="num">${t.rank ? Math.round(t.score) : "–"}</td>
    <td class="num">${pct(t.win_rate)}</td>
    <td class="num ${t.edge > 0 ? "pos" : t.edge < 0 ? "neg" : ""}">${t.n ? `${t.edge >= 0 ? "+" : "−"}${Math.abs(t.edge * 100).toFixed(1)}` : "–"}</td>
    <td class="num">${t.n ? pct(t.roi) : "–"}</td>
    <td class="num ${t.pnl > 0 ? "pos" : t.pnl < 0 ? "neg" : ""}">${usdShort(t.pnl, { sign: true })}</td>
    <td class="num">${t.n}</td>
    <td class="num">${usdShort(t.median_bet)}</td></tr>`).join("");
  root.innerHTML = head + `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function click(e, rerender) {
  const th = e.target.closest("th[data-sort]");
  if (!th) return;
  const key = th.dataset.sort;
  if (sortKey === key) sortDir = -sortDir;
  else { sortKey = key; sortDir = ["rank", "name"].includes(key) ? 1 : -1; }
  rerender();
}
