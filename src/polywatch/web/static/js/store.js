// Client state: one snapshot on load, then live events from the server. Views read from `state`.

export const state = {
  ready: false,
  items: new Map(),     // key -> bet
  scores: {},
  prices: {},
  markets: {},          // slug -> {start_ts, end_ts, closed, category, icon}
  watched: {},          // wallet -> tracked trader
  traders: [],
  common: [],
  positions: { positions: [], holdings: {} },
  status: {},
  settings: {},
  short: { windows: [], index: null, model: null, coins: [], intervals: [], bot_badges: [] },
  fresh: new Set(),     // keys that just arrived, flashed once
  connected: false,
};

const listeners = new Set();
let pending = new Set();
let scheduled = false;

// What changed since the last render: "items", "prices", "short", ... Views re-render only for their parts.
export function onChange(fn) { listeners.add(fn); }

function changed(part) {
  pending.add(part);
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    const parts = pending;
    pending = new Set();
    for (const fn of listeners) fn(parts);
  });
}

function applySnapshot(s) {
  state.items = new Map(s.items.map((i) => [i.key, i]));
  state.scores = s.scores || {};
  state.prices = s.prices || {};
  state.markets = s.markets || {};
  state.watched = s.watched || {};
  state.traders = s.traders || [];
  state.common = s.common || [];
  state.positions = s.positions || { positions: [], holdings: {} };
  state.status = s.status || {};
  state.settings = s.settings || {};
  state.short = s.short || state.short;
  state.ready = true;
  changed("all");
}

const handlers = {
  items(list) {
    for (const i of list) {
      if (!state.items.has(i.key)) state.fresh.add(i.key);
      state.items.set(i.key, i);
    }
    changed("items");
  },
  remove(keys) { for (const k of keys) state.items.delete(k); changed("items"); },
  scores(s) { state.scores = s; changed("scores"); },
  prices(batch) { Object.assign(state.prices, batch); changed("prices"); },
  markets(m) { Object.assign(state.markets, m); changed("markets"); },
  watched(w) { state.watched = w; changed("traders"); },
  traders(t) { state.traders = t; changed("traders"); },
  common(c) { state.common = c; changed("common"); },
  positions(p) { state.positions = p; changed("positions"); },
  status(s) { state.status = s; changed("status"); },
  short_windows(w) { state.short.windows = w; changed("short"); },
  short_bet() { /* arrives with the next short_windows push */ },
  short_index(p) { state.short.index = p; changed("short_index"); },
  short_board_changed() { changed("short_board"); },
};

let hellos = 0;
const extra = new Map(); // event -> [fn], for alerts and toasts
export function on(event, fn) { if (!extra.has(event)) extra.set(event, []); extra.get(event).push(fn); }

export async function connect() {
  const res = await fetch("/api/snapshot");
  applySnapshot(await res.json());
  const source = new EventSource("/api/events");
  source.onopen = () => { state.connected = true; changed("status"); };
  source.onerror = () => {
    state.connected = false;
    changed("status");
    if (source.readyState === EventSource.CLOSED) setTimeout(() => location.reload(), 3000);
  };
  for (const name of [...Object.keys(handlers), "alert", "toast", "hello"]) {
    source.addEventListener(name, (ev) => {
      const data = JSON.parse(ev.data);
      if (name === "hello") {  // a reconnect: catch up on what was missed
        if (hellos++ > 0) fetch("/api/snapshot").then((r) => r.json()).then(applySnapshot);
        return;
      }
      handlers[name]?.(data);
      for (const fn of extra.get(name) || []) fn(data);
    });
  }
}

export function refresh(part) { changed(part); }
