// Formatting and small DOM helpers shared by every view.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const now = () => Date.now() / 1000;

export function cents(p) {
  if (p == null || Number.isNaN(p)) return "–";
  const c = p * 100;
  if (c > 0 && c < 1) return "<1¢";
  if (c > 99 && c < 100) return ">99¢";
  return `${Math.round(c)}¢`;
}

export function signedCents(d) {
  const c = Math.round(d * 100);
  return c === 0 ? "±0¢" : `${c > 0 ? "+" : "−"}${Math.abs(c)}¢`;
}

export function usd(x, { sign = false } = {}) {
  if (x == null || Number.isNaN(x)) return "–";
  const s = sign ? (x > 0 ? "+" : x < 0 ? "−" : "") : x < 0 ? "−" : "";
  const a = Math.abs(x);
  const body = a >= 100 ? Math.round(a).toLocaleString("en-US") : a.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${s}$${body}`;
}

export function usdShort(x, { sign = false } = {}) {
  if (x == null || Number.isNaN(x)) return "–";
  const s = sign ? (x > 0 ? "+" : x < 0 ? "−" : "") : x < 0 ? "−" : "";
  const a = Math.abs(x);
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  return `${s}$${Math.round(a)}`;
}

export const pct = (x, digits = 0) => (x == null || Number.isNaN(x) ? "–" : `${(x * 100).toFixed(digits)}%`);
export const pts = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)} pts`;
export const payout = (p) => (p > 0 && p < 1 ? `${(1 / p).toFixed(2)}x` : "–");

export function duration(sec) {
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return h ? `${d}d ${h}h` : `${d}d`;
  if (h > 0) return m ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return `${m}m`;
  return `${sec}s`;
}

export function clock(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export const ago = (ts) => {
  const s = now() - ts;
  return s < 45 ? "just now" : `${duration(s)} ago`;
};

// When a market settles, as short words: "in 2h 10m", "live 40m", "ends in 3d".
export function settles(timing, t = now()) {
  if (!timing) return "";
  if (timing.closed) return "resolved";
  if (timing.start_ts) {
    if (t < timing.start_ts) return `starts in ${duration(timing.start_ts - t)}`;
    return `live ${duration(t - timing.start_ts)}`;
  }
  if (timing.end_ts) return t < timing.end_ts ? `ends in ${duration(timing.end_ts - t)}` : "ending";
  return "";
}

// Seconds until the market settles, for filtering and sorting; games count from kick-off.
export function secondsToSettle(timing, t = now()) {
  if (!timing) return Infinity;
  const at = timing.start_ts || timing.end_ts;
  return at ? Math.max(0, at - t) : Infinity;
}

export const shortWallet = (w) => (w ? `${w.slice(0, 6)}…${w.slice(-4)}` : "");

// Accounts without a username get "0xABC…-1777575277609" as a name; show a short wallet instead.
export const displayName = (name, wallet) => (!name || /^0x[0-9a-fA-F]{40}/.test(name) ? shortWallet(wallet) : name);

export function scoreRing(score, label = "copy") {
  const r = 24, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, score ?? 0));
  const cls = v >= 60 ? "hi" : v >= 30 ? "mid" : "lo";
  return `<div class="score ${cls}" title="How worth copying this bet is right now, 0–100">
    <svg viewBox="0 0 56 56"><circle class="bg" cx="28" cy="28" r="${r}"/><circle class="fg" cx="28" cy="28" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - v / 100)}"/></svg>
    <b>${score == null ? "–" : v}</b><small>${label}</small></div>`;
}

// The odds rail: 0–100¢ with the price paid and the price now.
export function oddsRail(entry, current, { left = "paid", right = "now" } = {}) {
  const e = Math.max(0, Math.min(1, entry)) * 100;
  const hasNow = current != null;
  const n = hasNow ? Math.max(0, Math.min(1, current)) * 100 : e;
  const dir = !hasNow || Math.abs(n - e) < 0.5 ? "" : n > e ? "up" : "down";
  const span = dir ? `<i class="span ${dir}" style="left:${Math.min(e, n)}%;width:${Math.abs(n - e)}%"></i>` : "";
  return `<div class="rail-wrap">
    <div class="rail-nums"><span>${left} <b>${cents(entry)}</b></span><span>${right} <b>${hasNow ? cents(current) : "–"}</b>${hasNow && dir ? ` <span class="${dir === "up" ? "pos" : "neg"}">${signedCents(current - entry)}</span>` : ""}</span></div>
    <div class="rail">${span}<i class="mark entry" style="left:${e}%"></i>${hasNow ? `<i class="mark now ${dir}" style="left:${n}%"></i>` : ""}</div>
    <div class="rail-foot"><span>pays ${payout(entry)}</span><span>${hasNow ? `pays ${payout(current)} now` : "waiting for price"}</span></div>
  </div>`;
}

export function thumb(icon) {
  return `<div class="thumb" ${icon ? `style="background-image:url('${esc(icon)}')"` : ""}></div>`;
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}
