// Filter sidebar built from a per-tab schema. Values live in the URL (#tab?key=value), so views can be bookmarked.

import { esc } from "./util.js";

const values = {};   // tab -> {key: value}
let current = null;  // {tab, schema, onChange}
let signature = "";

function defaults(schema) {
  const out = {};
  for (const f of schema) {
    if (f.type === "pair") { out[f.key + "Min"] = ""; out[f.key + "Max"] = ""; }
    else out[f.key] = f.default ?? (f.type === "chips" || f.type === "checklist" ? [] : f.type === "toggle" ? false : "");
  }
  return out;
}

function parse(schema, params) {
  const out = defaults(schema);
  for (const f of schema) {
    const keys = f.type === "pair" ? [f.key + "Min", f.key + "Max"] : [f.key];
    for (const k of keys) {
      if (!params.has(k)) continue;
      const raw = params.get(k);
      if (f.type === "chips" || f.type === "checklist") out[k] = raw ? raw.split(",") : [];
      else if (f.type === "toggle") out[k] = raw === "1";
      else if (f.type === "slider") out[k] = Number(raw);
      else out[k] = raw;
    }
  }
  return out;
}

function serialize(schema, v) {
  const d = defaults(schema);
  const params = new URLSearchParams();
  for (const [k, val] of Object.entries(v)) {
    const same = Array.isArray(val) ? val.join(",") === (d[k] || []).join(",") : val === d[k];
    if (same) continue;
    params.set(k, Array.isArray(val) ? val.join(",") : typeof val === "boolean" ? (val ? "1" : "0") : String(val));
  }
  return params.toString();
}

export function get(tab) { return values[tab] || {}; }

export function set(tab, key, value) {
  values[tab][key] = value;
  writeUrl();
  current?.onChange();
}

function writeUrl() {
  if (!current) return;
  const q = serialize(current.schema, values[current.tab]);
  history.replaceState(null, "", `#${current.tab}${q ? "?" + q : ""}`);
  const n = activeCount();
  document.getElementById("filter-badge").textContent = n ? `(${n})` : "";
}

export function activeCount() {
  if (!current) return 0;
  const d = defaults(current.schema);
  return Object.entries(values[current.tab]).filter(([k, v]) =>
    Array.isArray(v) ? v.length > 0 && v.join(",") !== (d[k] || []).join(",") : v !== d[k]).length;
}

// Show a tab's filters. `query` is the part of the hash after "?".
export function mount(tab, schema, query, onChange) {
  if (!values[tab] || query != null) values[tab] = parse(schema, new URLSearchParams(query || ""));
  current = { tab, schema, onChange };
  signature = "";
  render();
  writeUrl();
}

export function reset() {
  if (!current) return;
  values[current.tab] = defaults(current.schema);
  signature = "";
  render();
  writeUrl();
  current.onChange();
}

// Re-render when option lists change (a new category appears), without disturbing an input being typed in.
export function refreshOptions() {
  if (!current) return;
  const sig = current.schema.map((f) => (typeof f.options === "function" ? f.options().map((o) => o.value + (o.sub ?? "")).join("|") : "")).join("#");
  if (sig !== signature) render();
}

function options(f) { return typeof f.options === "function" ? f.options() : f.options || []; }

function render() {
  const { tab, schema } = current;
  const v = values[tab];
  signature = schema.map((f) => (typeof f.options === "function" ? f.options().map((o) => o.value + (o.sub ?? "")).join("|") : "")).join("#");
  const active = document.activeElement?.id;
  const html = schema.map((f) => {
    const id = `f-${tab}-${f.key}`;
    const hint = f.hint ? `<div class="f-hint">${esc(f.hint)}</div>` : "";
    switch (f.type) {
      case "search":
        return `<div class="f-group"><input class="input" type="search" id="${id}" data-key="${f.key}" placeholder="${esc(f.placeholder || "Search")}" value="${esc(v[f.key])}" aria-label="${esc(f.label)}"></div>`;
      case "seg":
        return `<div class="f-group"><div class="f-label">${esc(f.label)}</div><div class="seg" role="group">${options(f).map((o) =>
          `<button data-key="${f.key}" data-value="${esc(o.value)}" aria-pressed="${v[f.key] === o.value}">${esc(o.label)}</button>`).join("")}</div>${hint}</div>`;
      case "chips": {
        const opts = options(f);
        if (!opts.length) return "";
        return `<div class="f-group"><div class="f-label">${esc(f.label)}${v[f.key].length ? `<button class="link-btn" data-clear="${f.key}">All</button>` : ""}</div><div class="chips">${opts.map((o) =>
          `<button class="chip" data-key="${f.key}" data-chip="${esc(o.value)}" aria-pressed="${v[f.key].includes(o.value)}">${esc(o.label)}</button>`).join("")}</div>${hint}</div>`;
      }
      case "toggle":
        return `<div class="f-group"><label class="toggle"><span>${esc(f.label)}</span><input type="checkbox" id="${id}" data-key="${f.key}" ${v[f.key] ? "checked" : ""}></label>${hint}</div>`;
      case "slider": {
        const fmt = f.format || ((x) => x);
        return `<div class="f-group"><div class="f-label"><label for="${id}">${esc(f.label)}</label><output id="${id}-out">${esc(fmt(v[f.key]))}</output></div>
          <input type="range" id="${id}" data-key="${f.key}" min="${f.min}" max="${f.max}" step="${f.step || 1}" value="${v[f.key]}">${hint}</div>`;
      }
      case "pair":
        return `<div class="f-group"><div class="f-label">${esc(f.label)}</div><div class="pair">
          <input class="input" type="number" inputmode="decimal" id="${id}-min" data-key="${f.key}Min" placeholder="${esc(f.placeholders?.[0] ?? "min")}" value="${esc(v[f.key + "Min"])}" aria-label="${esc(f.label)} minimum">
          <span>to</span>
          <input class="input" type="number" inputmode="decimal" id="${id}-max" data-key="${f.key}Max" placeholder="${esc(f.placeholders?.[1] ?? "max")}" value="${esc(v[f.key + "Max"])}" aria-label="${esc(f.label)} maximum"></div>${hint}</div>`;
      case "select":
        return `<div class="f-group"><div class="f-label"><label for="${id}">${esc(f.label)}</label></div><select class="select" id="${id}" data-key="${f.key}">${options(f).map((o) =>
          `<option value="${esc(o.value)}" ${String(v[f.key]) === String(o.value) ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>${hint}</div>`;
      case "checklist": {
        const opts = options(f);
        if (!opts.length) return "";
        return `<div class="f-group"><div class="f-label">${esc(f.label)}${v[f.key].length ? `<button class="link-btn" data-clear="${f.key}">All</button>` : ""}</div>
          <input class="input" type="search" id="${id}-q" data-filter-list="${f.key}" placeholder="Find…" aria-label="Find in ${esc(f.label)}">
          <div class="checklist" data-list="${f.key}">${opts.map((o) =>
            `<label data-text="${esc(o.label.toLowerCase())}"><input type="checkbox" data-key="${f.key}" data-check="${esc(o.value)}" ${v[f.key].includes(o.value) ? "checked" : ""}>${esc(o.label)}${o.sub ? `<span class="sub">${esc(o.sub)}</span>` : ""}</label>`).join("")}</div>${hint}</div>`;
      }
      default:
        return "";
    }
  }).join("");
  const body = document.getElementById("filter-body");
  body.innerHTML = html;
  if (active && document.getElementById(active)) {
    const el = document.getElementById(active);
    el.focus();
    if (el.setSelectionRange && el.type === "search") el.setSelectionRange(el.value.length, el.value.length);
  }
}

export function bind() {
  const body = document.getElementById("filter-body");
  body.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn || !current) return;
    const v = values[current.tab];
    if (btn.dataset.clear) { set(current.tab, btn.dataset.clear, []); render(); return; }
    if (btn.dataset.chip != null) {
      const key = btn.dataset.key, val = btn.dataset.chip;
      const list = v[key].includes(val) ? v[key].filter((x) => x !== val) : [...v[key], val];
      set(current.tab, key, list);
      render();
    } else if (btn.dataset.value != null) {
      set(current.tab, btn.dataset.key, btn.dataset.value);
      render();
    }
  });
  body.addEventListener("input", (e) => {
    const el = e.target;
    if (!current) return;
    if (el.dataset.filterList) {
      const q = el.value.toLowerCase();
      for (const label of body.querySelectorAll(`[data-list="${el.dataset.filterList}"] label`)) label.hidden = !label.dataset.text.includes(q);
      return;
    }
    const key = el.dataset.key;
    if (!key) return;
    if (el.type === "checkbox" && el.dataset.check != null) {
      const list = values[current.tab][key];
      set(current.tab, key, el.checked ? [...list, el.dataset.check] : list.filter((x) => x !== el.dataset.check));
    } else if (el.type === "checkbox") set(current.tab, key, el.checked);
    else if (el.type === "range") {
      set(current.tab, key, Number(el.value));
      const f = current.schema.find((x) => x.key === key);
      document.getElementById(`${el.id}-out`).textContent = (f.format || ((x) => x))(Number(el.value));
    } else set(current.tab, key, el.value);
  });
}

// Helpers for views applying filters.
export const num = (s) => (s === "" || s == null || Number.isNaN(Number(s)) ? null : Number(s));
