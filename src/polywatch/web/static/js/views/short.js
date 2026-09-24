// Short markets: live 5-minute, 15-minute and hourly up-or-down windows with the crowd's view, and the leaderboard.

import { state } from "../store.js";
import { get, num } from "../filters.js";
import { ago, cents, clock, displayName, esc, pct, usdShort } from "../util.js";

const INTERVAL_NAMES = { "5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour" };
const BADGES = ["ARB", "SNIPE", "LAST-SEC", "HFT", "MAKER"];
const BADGE_HELP = {
  ARB: "Often buys Up and Down in the same window, locking in a result.",
  SNIPE: "Most of its money goes in at 95¢ or more.",
  "LAST-SEC": "Usually still buying in the last seconds of a window.",
  HFT: "Dozens of fills per window, like an automated strategy.",
  MAKER: "Often sells pairs it minted, like a market maker.",
};

export const schema = [
  { type: "chips", key: "coins", label: "Coins", options: () => state.short.coins.map((c) => ({ value: c, label: c.toUpperCase() })) },
  { type: "chips", key: "intervals", label: "Window length", options: () => state.short.intervals.map((i) => ({ value: i, label: INTERVAL_NAMES[i] || i })) },
  { type: "toggle", key: "hideBots", label: "Hide bots", hint: "Traders badged ARB, SNIPE, LAST-SEC or HFT. You can't copy them by hand." },
  { type: "seg", key: "period", label: "Leaderboard period", default: "7d", options: [
    { value: "24h", label: "24h" }, { value: "7d", label: "7 days" }, { value: "30d", label: "30 days" }] },
  { type: "select", key: "minWindows", label: "Windows traded at least", default: "20", options: [
    { value: "10", label: "10" }, { value: "20", label: "20" }, { value: "50", label: "50" }, { value: "100", label: "100" }, { value: "250", label: "250" }] },
  { type: "pair", key: "profit", label: "Profit ($)", placeholders: ["any", "any"] },
  { type: "chips", key: "hideBadges", label: "Hide traders badged", options: BADGES.map((b) => ({ value: b, label: b })) },
  { type: "toggle", key: "starred", label: "Only starred traders" },
];

// --- leaderboard data (fetched from the server, which aggregates the indexed history) ---------------
let board = { key: "", rows: [], loading: false, error: "" };
let sortKey = "pnl", sortDir = -1;
let rerenderRef = () => {};

function boardKey() {
  const f = get("short");
  return new URLSearchParams({ period: f.period || "7d", coins: (f.coins || []).join(","), intervals: (f.intervals || []).join(","),
    min_windows: f.minWindows || "20" }).toString();
}

async function loadBoard(force = false) {
  const key = boardKey();
  if (!force && (board.key === key || board.loading)) return;
  board.loading = true;
  board.key = key;
  try {
    const res = await fetch(`/api/short/board?${key}`);
    const data = await res.json();
    if (board.key === key) { board.rows = data.rows || []; board.error = ""; }
  } catch (e) {
    board.error = "Couldn't load the leaderboard. It retries on the next update.";
  } finally {
    board.loading = false;
    rerenderRef();
  }
}

export function boardChanged() { loadBoard(true); }

// --- rendering ------------------------------------------------------------------------------------
function visibleBet(b, f) {
  if (f.hideBots && b.is_bot) return false;
  if (f.hideBadges?.length && b.badges.some((x) => f.hideBadges.includes(x))) return false;
  return true;
}

function windowCard(w, f) {
  const t = Date.now() / 1000;
  const left = w.end_ts - t;
  const up = w.outcomes[0] || "Up", down = w.outcomes[1] || "Down";
  const p = w.crowd.p;
  const market = w.price;
  const lead = p ?? market;
  const leadUp = lead == null ? null : lead >= 0.5;
  const shown = lead == null ? null : Math.min(99, Math.max(1, Math.round((leadUp ? lead : 1 - lead) * 100)));
  let big = lead == null ? "–" : `${leadUp ? up : down} ${shown}%`;
  const [cUp, cDown] = [w.crowd.first_n, w.crowd.second_n];
  let what = p != null && cUp + cDown > 0
    ? `${cUp + cDown} proven winner${cUp + cDown === 1 ? "" : "s"} bought: ${cUp} ${esc(up)}, ${cDown} ${esc(down)} · market ${cents(market)} ${esc(up)}`
    : market != null ? `market price${p != null ? " · no proven winners in yet" : ""}` : "waiting for the first trade";
  if (w.closed) {
    const settled = market != null && (market >= 0.9 || market <= 0.1);
    big = settled ? `${leadUp ? up : down} won` : "Settling";
    what = settled ? "going by the last trades; the official result follows" : "waiting for the result";
  }
  const [uUsd, dUsd] = w.followed.usd, [uN, dN] = w.followed.n;
  const share = uUsd + dUsd > 0 ? (uUsd / (uUsd + dUsd)) * 100 : 50;
  const bets = w.bets.filter((b) => visibleBet(b, f)).slice(0, 6);
  const railMarket = market != null ? `<i class="mark now ${market >= 0.5 ? "up" : "down"}" style="left:${market * 100}%" title="Market ${cents(market)}"></i>` : "";
  const railCrowd = p != null ? `<i class="mark crowd" style="left:${p * 100}%" title="Crowd ${pct(p)}"></i>` : "";
  return `<article class="win${w.closed ? " closed" : ""}">
    <div class="win-head">
      <div><div class="win-coin">${esc(w.coin.toUpperCase())}<small>${esc(INTERVAL_NAMES[w.interval] || w.interval)}</small></div>
        <div class="win-time">${esc(w.label.split("·")[1]?.trim() || "")}</div></div>
      <div class="countdown${left < 60 && left > 0 ? " soon" : ""}" data-ends="${w.end_ts}">${w.closed ? "Closed" : clock(left)}<small>${w.closed ? "settling" : "left"}</small></div>
    </div>
    <div class="verdict"><div class="big ${leadUp == null ? "" : leadUp ? "up" : "down"}">${esc(big)}</div><div class="what">${what}</div></div>
    <div class="rail-wrap">
      <div class="rail"><i class="tick"></i>${railMarket}${railCrowd}</div>
      <div class="rail-foot"><span>${esc(down)}</span><span>${esc(up)}</span></div>
    </div>
    <div>
      <div class="split-label">Top-profit traders' money</div>
      <div class="split" title="What the top traders by profit bought in this window"><i class="u" style="width:${share}%"></i><i class="d" style="width:${100 - share}%"></i></div>
      <div class="split-nums"><span class="pos">${esc(up)} ${usdShort(uUsd)} · ${uN} trader${uN === 1 ? "" : "s"}</span><span class="neg">${dN} trader${dN === 1 ? "" : "s"} · ${usdShort(dUsd)} ${esc(down)}</span></div>
    </div>
    <div class="win-bets">${bets.length ? bets.map((b) => `<div class="win-bet">
        <button class="who" data-short-wallet="${esc(b.wallet)}">${b.rank ? `#${b.rank} ` : ""}${esc(displayName(b.name, b.wallet))}${b.is_bot ? " ·bot" : ""}</button>
        <span><span class="pill ${b.outcome === up ? "up" : "down"}">${b.side === "BUY" ? "" : "sold "}${esc(b.outcome)}</span> ${usdShort(b.usd)} @ ${cents(b.price)}</span>
        <time data-ago="${b.ts}">${ago(b.ts)}</time></div>`).join("")
      : `<div class="win-empty">No bets from top-profit traders yet.</div>`}</div>
    <a class="link-btn" href="${esc(w.url)}" target="_blank" rel="noopener" style="justify-self:start;padding:0">Open on Polymarket ↗</a>
  </article>`;
}

function modelNote() {
  const m = state.short.model;
  const idx = state.short.index;
  if (!m) {
    const days = idx?.oldest_ts ? (Date.now() / 1000 - idx.oldest_ts) / 86400 : 0;
    return `<div class="model-note">The crowd probability appears once 3 days of history are indexed (${days.toFixed(1)} so far). Until then, cards show the market price and which side followed traders are on.</div>`;
  }
  if (!m.n) return `<div class="model-note">Not enough recent windows with bets from proven traders to check the crowd probability yet.</div>`;
  if (!m.edge) {
    return `<div class="model-note">Checked on ${m.n} recent windows, the crowd signal <b>${m.n < 200 ? "hasn't been checked on enough windows yet" : "didn't beat the market price"}</b>, so cards show the market price instead of a probability. It's checked again every 15 minutes.</div>`;
  }
  const delay = m.copy_delay_s || 20;
  const gap = m.lean_win_rate - m.lean_avg_price;
  const lean = !m.lean_n ? "" : gap > 0.02
    ? ` When it leaned 10+ points from the market, that side won <b>${pct(m.lean_win_rate)}</b> of ${m.lean_n} windows, and buying it ${delay} seconds later cost <b>${cents(m.lean_avg_price)}</b> on average: an edge of about ${Math.round(gap * 100)} points left for someone copying by hand.`
    : ` But when it leaned 10+ points from the market, the price ${delay} seconds later had already caught up (${cents(m.lean_avg_price)} for a side that won ${pct(m.lean_win_rate)}): <b>fast traders take the edge before it can be copied by hand</b>. Treat it as a read on the market, not a bet signal.`;
  return `<div class="model-note"><b>Crowd probability</b>: the market price, nudged toward the side that ${m.members} proven short-market traders are on, weighted by how often they win and how much they put in. Checked on ${m.n} recent windows it hadn't seen, it called the winner <b>${pct(m.model_accuracy)}</b> of the time against <b>${pct(m.market_accuracy)}</b> for the market price alone.${lean}</div>`;
}

function progress() {
  const idx = state.short.index;
  if (!idx || !idx.total || idx.pending === 0) return "";
  const share = (idx.done / idx.total) * 100;
  return `<div class="progress"><div>Building history: <b>${idx.done.toLocaleString()}</b> of ${idx.total.toLocaleString()} windows from the last 7 days. The leaderboard fills in as it goes.</div>
    <div class="bar"><i style="width:${share}%"></i></div></div>`;
}

const COLUMNS = [
  { key: "rank", label: "#", num: true, get: (r) => r.rank },
  { key: "name", label: "Trader", get: (r) => (r.name || r.wallet).toLowerCase() },
  { key: "pnl", label: "Profit", num: true, get: (r) => r.pnl },
  { key: "consistency", label: "Consistency", num: true, get: (r) => r.consistency },
  { key: "win_rate", label: "Win rate", num: true, get: (r) => r.win_rate },
  { key: "avg_price", label: "Avg price", num: true, get: (r) => r.avg_price },
  { key: "windows", label: "Windows", num: true, get: (r) => r.windows },
  { key: "roi", label: "Return", num: true, get: (r) => r.roi },
  { key: "volume", label: "Volume", num: true, get: (r) => r.volume },
];

function leaderboard(f) {
  const [pMin, pMax] = [num(f.profitMin), num(f.profitMax)];
  let rows = board.rows.filter((r) => {
    if (f.hideBots && r.is_bot) return false;
    if (f.hideBadges?.length && r.badges.some((b) => f.hideBadges.includes(b))) return false;
    if (f.starred && !r.star) return false;
    if (pMin != null && r.pnl < pMin) return false;
    if (pMax != null && r.pnl > pMax) return false;
    return true;
  });
  const col = COLUMNS.find((c) => c.key === sortKey) || COLUMNS[2];
  rows = [...rows].sort((a, b) => { const x = col.get(a), y = col.get(b); return (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
  const period = { "24h": "24 hours", "7d": "7 days", "30d": "30 days" }[f.period || "7d"];
  const title = `<h2 class="section-title">Leaderboard <small>${rows.length} traders · last ${period}${board.loading ? " · updating…" : ""}</small></h2>`;
  if (board.error) return title + `<div class="empty"><p>${esc(board.error)}</p></div>`;
  if (!rows.length) return title + `<div class="empty"><h3>${board.rows.length ? "No traders match these filters" : "No history yet"}</h3><p>${board.rows.length ? "Loosen a filter on the left." : "Traders appear once they have enough indexed windows."}</p></div>`;
  const th = COLUMNS.map((c) => `<th class="sortable${c.num ? " num" : ""}${c.key === sortKey ? " sorted" : ""}" data-sort="${c.key}">${c.label}${c.key === sortKey ? (sortDir > 0 ? " ↑" : " ↓") : ""}</th>`).join("");
  return title + `<div class="table-wrap"><table><thead><tr><th></th>${th}</tr></thead><tbody>${rows.slice(0, 300).map((r) => `<tr data-short-wallet="${esc(r.wallet)}">
    <td style="width:1%"><button class="star-btn${r.star ? " on" : ""}" data-star="${esc(r.wallet)}" aria-label="${r.star ? "Unstar" : "Star"} ${esc(displayName(r.name, r.wallet))}" title="Star: always follow live">${r.star ? "★" : "☆"}</button><button class="star-btn${r.bell ? " on" : ""}" data-bell="${esc(r.wallet)}" aria-label="Alerts for ${esc(displayName(r.name, r.wallet))}" title="Alert me when they bet">${r.bell ? "🔔" : "🔕"}</button></td>
    <td class="rank">${r.rank}</td>
    <td><div class="name-cell"><span class="n">${esc(displayName(r.name, r.wallet))}</span>${r.followed ? `<span class="pill iris" title="Their bets show on the live cards">live</span>` : ""}${r.badges.map((b) => `<span class="pill warn" title="${esc(BADGE_HELP[b] || b)}">${b}</span>`).join("")}</div></td>
    <td class="num ${r.pnl >= 0 ? "pos" : "neg"}">${usdShort(r.pnl, { sign: true })}</td>
    <td class="num"><div style="display:flex;align-items:center;gap:8px;justify-content:flex-end"><div class="bar" style="width:60px"><i style="width:${r.consistency}%"></i></div>${Math.round(r.consistency)}</div></td>
    <td class="num">${pct(r.win_rate)}</td>
    <td class="num">${cents(r.avg_price)}</td>
    <td class="num">${r.windows.toLocaleString()}</td>
    <td class="num">${pct(r.roi, 1)}</td>
    <td class="num">${usdShort(r.volume)}</td></tr>`).join("")}</tbody></table></div>`;
}

export function render(root, rerender) {
  rerenderRef = rerender;
  loadBoard();
  const f = get("short");
  const windows = state.short.windows.filter((w) =>
    (!f.coins?.length || f.coins.includes(w.coin)) && (!f.intervals?.length || f.intervals.includes(w.interval)));
  const groups = ["5m", "15m", "1h"].map((iv) => {
    const list = windows.filter((w) => w.interval === iv);
    if (!list.length) return "";
    return `<h2 class="section-title">${INTERVAL_NAMES[iv]} windows <small>${list.filter((w) => !w.closed).length} open</small></h2>
      <div class="windows">${list.map((w) => windowCard(w, f)).join("")}</div>`;
  }).join("");
  root.innerHTML = `<div class="view-head"><div><h1>Short markets</h1>
      <p>Crypto up-or-down windows that settle in 5 minutes, 15 minutes or an hour. See who wins most on them, what they're betting right now, and which way the proven winners lean.</p></div></div>
    ${progress()}${modelNote()}
    ${groups || `<div class="empty"><h3>No open windows match</h3><p>Pick another coin or window length on the left.</p></div>`}
    ${leaderboard(f)}`;
}

export function click(e, rerender) {
  const th = e.target.closest("th[data-sort]");
  if (th) {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir = -sortDir;
    else { sortKey = key; sortDir = ["rank", "name", "avg_price"].includes(key) ? 1 : -1; }
    rerender();
    return true;
  }
  const star = e.target.closest("[data-star],[data-bell]");
  if (star) {
    e.stopPropagation();
    const wallet = star.dataset.star || star.dataset.bell;
    const row = board.rows.find((r) => r.wallet === wallet);
    const body = star.dataset.star ? { wallet, star: !row?.star } : { wallet, bell: !row?.bell };
    if (row) Object.assign(row, body);
    fetch("/api/short/pref", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    rerender();
    return true;
  }
  return false;
}

export function shortTrader(wallet) {
  return board.rows.find((r) => r.wallet === wallet) || null;
}

export function filtersChanged() { loadBoard(); }
