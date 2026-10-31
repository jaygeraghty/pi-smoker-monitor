/* Smoker settings page.
 *
 * Three pages, switched by the address after the # (so Back works):
 *   #now     what's happening, silence, end cook
 *   #probes  per-probe and per-pit alarms: name, target, too-cold limit
 *   #rules   which alarms are on, and their thresholds
 *
 * Talks to the Pi's own server (see api.py): GET/PUT /api/settings,
 * GET /api/status, POST /api/silence, POST /api/end-cook.
 *
 * Everything shown is built with textContent (never HTML strings), so a probe
 * name coming from the ETI app can't inject anything into the page.
 *
 * Settings are kept in °C. °F is only how they are shown and typed.
 */
"use strict";

/* ------------------------------------------------------------------ setup --- */

const STATUS_EVERY_MS = 5000;

/* The alarm rules shown on the Alarm rules page, in order. A row is a switch
 * ("on": a true/false rule), or a percentage ("pct": 0 means off, shown as a
 * switch so "off" is never a hidden 0). `minutes` / `degrees` add a number that
 * applies while it is on. `brand` hides the row if you don't have that kind. */
function ruleSections(brands) {
  const eti = brands.eti, traeger = brands.traeger;
  const services = [eti && "ETI", traeger && "Traeger"].filter(Boolean).join(" or ");
  return [
    { title: "Temperatures",
      footer: "Limits come from the ETI app unless you set your own for a probe or pit (Probes page).",
      rows: [
        eti && { on: "pit_high", name: "Pit too hot", hint: "Above its high limit" },
        eti && { on: "pit_low", name: "Pit too cold", hint: "Below its low limit: the fire may be going out" },
        { on: "probe_high", name: "Meat reached its target", hint: "The main one: your food is done" },
        { on: "probe_low", name: "Meat too cold", hint: "Below its low limit. Rarely needed" },
      ] },
    { title: "Lost contact",
      footer: "How long a device can go quiet before the alarm rings. While a whole service can't be reached, its devices' “gone quiet” alarms wait, and the “can't reach” alarm rings instead.",
      rows: [
        eti && { on: "gateway_timeout", name: "RFX Gateway gone quiet", minutes: "gateway_timeout_minutes" },
        { on: "probe_timeout", name: "Probe gone quiet", hint: "Only probes used in this cook", minutes: "probe_timeout_minutes" },
        traeger && { on: "grill_timeout", name: "Grill gone quiet", hint: "Only while the grill is switched on", minutes: "grill_timeout_minutes" },
        { on: "source_unreachable", name: `Can't reach ${services}`, hint: "The Pi has lost the internet, or the service is down", minutes: "source_unreachable_minutes" },
      ] },
    traeger && { title: "Traeger grill",
      footer: "Only while the grill is cooking: not while lighting, heating up or cooling down.",
      rows: [
        { on: "grill_off_target", name: "Grill off its set temperature", hint: "Too far above or below what it's set to", degrees: "grill_off_target_c" },
        { pct: "pellets_low_pct", name: "Pellets running low", hint: "Needs the grill's pellet sensor" },
      ] },
    { title: "Batteries",
      rows: [
        eti && { pct: "gateway_battery_pct", name: "RFX Gateway battery low" },
        { pct: "probe_battery_pct", name: "Probe battery low" },
      ] },
  ].filter(Boolean).map((s) => ({ ...s, rows: s.rows.filter(Boolean) }));
}

/* ------------------------------------------------------------------ state --- */

let config = null;      // from GET /api/settings: ranges, presets, recommended, brands
let revision = 0;       // the settings revision we are editing from
let saved = null;       // what the Pi has:  { rules, units, devices }
let draft = null;       // what is on screen
let live = null;        // latest GET /api/status
let liveError = false;  // couldn't reach the Pi
let lastOn = {};        // remembers a % while its switch is off
const typed = {};       // text typed in a number box that isn't valid yet, by field id
const problems = {};    // message for each invalid number box, by field id
const etiSeen = {};     // the ETI app's limit for each device side, when we've seen it
let saving = false;

const $ = (id) => document.getElementById(id);
const clone = (v) => JSON.parse(JSON.stringify(v));

/* Build an element. Text is always set as text. */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled", "hidden"].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}

/* -------------------------------------------------------------- temperatures --- */

const isF = () => draft.units === "F";
const toDisplay = (c) => (isF() ? c * 9 / 5 + 32 : c);          // a temperature
const fromDisplay = (n) => (isF() ? Math.round(((n - 32) * 5 / 9) * 10) / 10 : n);
const diffToDisplay = (c) => (isF() ? Math.round(c * 9 / 5) : c);   // a temperature *difference*
const diffFromDisplay = (n) => (isF() ? Math.round(n * 5 / 9) : n);
const unitSign = () => (isF() ? "°F" : "°C");
const whole = (n) => Math.round(n);
function showTemp(c, units = draft ? draft.units : "C") {
  if (c == null) return "–";
  return units === "F" ? `${Math.round(c * 9 / 5 + 32)}°F` : `${Math.round(c * 10) / 10}°C`;
}

/* --------------------------------------------------------------- talking to the Pi --- */

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch { /* no body */ }
  return { ok: response.ok, status: response.status, data };
}

function adopt(data) {
  config = {
    ranges: data.ranges, presets: data.presets, recommended: data.recommended, brands: data.brands,
  };
  revision = data.revision;
  saved = normalise({ rules: data.rules, units: data.units, devices: data.devices });
  draft = clone(saved);
}

function normaliseDevice(d = {}) {
  const side = (s = {}) => ({ mode: s.mode || "eti", c: s.c ?? null });
  return { name: d.name || "", high: side(d.high), low: side(d.low) };
}
function normalise(model) {
  const devices = {};
  for (const [id, d] of Object.entries(model.devices)) devices[id] = normaliseDevice(d);
  return { rules: model.rules, units: model.units, devices };
}

/* What the monitor reports, as a list of the pits and probes we can set limits on. */
function knownDevices() {
  const out = [];
  const status = live && live.status;
  if (status) {
    for (const pit of status.pits) if (pit.kind === "gateway") out.push(deviceFrom(pit, "pit"));
    for (const probe of status.probes) out.push(deviceFrom(probe, "probe"));
  }
  const seen = new Set(out.map((d) => d.id));
  for (const id of Object.keys(draft.devices)) {
    if (!seen.has(id)) out.push({ id, kind: "probe", name: "", gone: true });
  }
  return out;
}
function deviceFrom(item, kind) {
  const isPit = kind === "pit";
  const eti = isPit
    ? { high: item.high_origin === "eti" ? item.high_c : undefined,
        low: item.low_origin === "eti" ? item.low_c : undefined }
    : { high: item.target_origin === "eti" ? item.target_c : undefined,
        low: item.low_origin === "eti" ? item.low_c : undefined };
  for (const side of ["high", "low"]) {
    if (eti[side] !== undefined) etiSeen[`${item.id}/${side}`] = eti[side];
    else if (!(`${item.id}/${side}` in etiSeen)) etiSeen[`${item.id}/${side}`] = null;
  }
  return {
    id: item.id, kind, name: item.name, fresh: item.fresh,
    temp: isPit ? item.temp_c : item.core_c, source: item.source,
  };
}
function nameOf(id) {
  const typedName = draft.devices[id] && draft.devices[id].name.trim();
  if (typedName) return typedName;
  const found = knownDevices().find((d) => d.id === id);
  return found && found.name ? found.name : "A probe that isn't reporting";
}

/* ---------------------------------------------------------------- derived info --- */

const ruleRows = () => ruleSections(config.brands).flatMap((s) => s.rows);
const isOn = (row, rules) => (row.on ? rules[row.on] : rules[row.pct] > 0);
const sideWords = (kind, side) =>
  kind === "pit" ? (side === "high" ? "pit too hot" : "pit too cold")
                 : (side === "high" ? "target alarm" : "too-cold alarm");

/* Everything that is switched off, so it is never hidden:  key -> words. */
function offAlarms(model, devices) {
  const off = new Map();
  for (const row of ruleRows()) if (!isOn(row, model.rules)) off.set(`rule:${row.on || row.pct}`, row.name);
  for (const [id, d] of Object.entries(model.devices)) {
    const kind = devices.find((x) => x.id === id)?.kind || "probe";
    for (const side of ["high", "low"]) {
      if (d[side].mode === "off") off.set(`dev:${id}:${side}`, `${nameOf(id)}: ${sideWords(kind, side)}`);
    }
  }
  return off;
}

/* The unsaved changes, in words (one entry per thing changed). */
function changeList() {
  const items = [];
  for (const row of ruleRows()) {
    const fields = [row.on, row.pct, row.minutes, row.degrees].filter(Boolean);
    if (fields.some((f) => saved.rules[f] !== draft.rules[f])) items.push(row.name);
  }
  if (saved.units !== draft.units) items.push("Temperatures in");
  const ids = new Set([...Object.keys(saved.devices), ...Object.keys(draft.devices)]);
  for (const id of ids) {
    const a = saved.devices[id] || normaliseDevice();
    const b = draft.devices[id] || normaliseDevice();
    if (JSON.stringify(a) !== JSON.stringify(b)) items.push(nameOf(id));
  }
  return items;
}
const hasProblems = () =>
  Object.keys(problems).length > 0 || Object.values(draft.devices).some(orderBad);

/* ------------------------------------------------------------------ rendering --- */

let currentTab = "now";
let focusAfter = null;   // id of the control to refocus after a redraw

function rerender(focusId) {
  focusAfter = focusId || null;
  renderView(currentTab);
  if (focusAfter) $(focusAfter)?.focus();
  renderChrome();
}

function renderView(tab) {
  if (!config) return;
  const target = $(`view-${tab}`);
  // A redraw must not take the keyboard or screen reader away from where they were.
  const active = document.activeElement;
  const refocus = active && target.contains(active) && active.id ? active.id : null;
  const view = { now: viewNow, probes: viewProbes, rules: viewRules }[tab]();
  target.replaceChildren(...view);
  if (refocus) $(refocus)?.focus({ preventScroll: true });
}

/* A number you can step with − / + or type. `field` has id, get(), set(n), min, max, step. */
function numberBox(field, label, unit, spoken) {
  const id = `num-${field.id}`;
  const value = field.get();
  const text = id in typed ? typed[id] : String(value);
  const invalid = id in problems;
  const input = h("input", {
    type: "text", inputmode: "numeric", pattern: "[0-9]*", id, value: text,
    "aria-labelledby": `what-${field.id} unit-${field.id}`,
    "aria-describedby": `err-${field.id}`,
    "aria-invalid": invalid ? "true" : false,
    autocomplete: "off",
    oninput: () => checkTyped(field, input),
    // Tidy the text (e.g. "093" -> "93") without redrawing: a redraw here would
    // swallow the tap on the − / + button that caused this blur.
    onblur: () => { if (!(id in problems)) input.value = String(field.get()); },
  });
  const step = (dir) => () => {
    delete typed[id]; delete problems[id];
    field.set(Math.min(field.max, Math.max(field.min, whole(field.get()) + dir * field.step)));
    rerender(`${dir < 0 ? "less" : "more"}-${field.id}`);
  };
  return [
    h("div", { class: "sub" },
      h("span", { class: "what", id: `what-${field.id}`, text: label }),
      h("div", { class: "stepper" },
        h("button", { type: "button", id: `less-${field.id}`, "aria-label": `Less ${spoken}`,
                      disabled: !invalid && value <= field.min, onclick: step(-1), text: "−" }),
        input,
        h("button", { type: "button", id: `more-${field.id}`, "aria-label": `More ${spoken}`,
                      disabled: !invalid && value >= field.max, onclick: step(1), text: "+" }),
        h("span", { class: "unit", id: `unit-${field.id}`, text: unit }))),
    h("p", { class: "error", id: `err-${field.id}`, role: "alert", hidden: !invalid },
      invalid ? [h("span", { "aria-hidden": "true", text: "⚠︎" }), h("span", { text: problems[id] })] : []),
  ];
}

/* Say what's wrong, and how to fix it, next to the box. */
function checkTyped(field, input) {
  const id = input.id, text = input.value.trim(), n = Number(text);
  let problem = "";
  if (text === "" || !Number.isInteger(n)) problem = "Enter a whole number";
  else if (n < field.min || n > field.max) problem = `Choose between ${field.min} and ${field.max}`;
  const error = $(`err-${field.id}`);
  if (problem) {
    typed[id] = input.value; problems[id] = problem;
    input.setAttribute("aria-invalid", "true");
    error.replaceChildren(h("span", { "aria-hidden": "true", text: "⚠︎" }), h("span", { text: problem }));
    error.hidden = false;
  } else {
    delete typed[id]; delete problems[id];
    input.removeAttribute("aria-invalid");
    error.hidden = true;
    field.set(n);
    syncStepper(field);
  }
  syncOrderMessages();
  renderChrome();
}

/* Grey out − / + at the ends of the range. */
function syncStepper(field) {
  // Quick-pick chips show which one matches the number now in the box.
  document.querySelectorAll(`[id^="${field.id}-preset-"]`).forEach((chip) => {
    chip.setAttribute("aria-pressed", String(whole(toDisplay(Number(chip.dataset.c))) === field.get()));
  });
  const less = $(`less-${field.id}`), more = $(`more-${field.id}`);
  if (less) less.disabled = field.get() <= field.min;
  if (more) more.disabled = field.get() >= field.max;
}

/* A device whose too-cold limit isn't below its target can't be saved. */
const orderBad = (d) =>
  d.high.mode === "set" && d.low.mode === "set" && d.high.c != null && d.low.c != null && d.low.c >= d.high.c;
function syncOrderMessages() {
  for (const [id, d] of Object.entries(draft.devices)) {
    const el = $(`order-${keyOf(id)}`);
    if (el) el.hidden = !orderBad(d);
  }
}
const keyOf = (id) => id.replace(/[^A-Za-z0-9_-]/g, "_");

function switchRow(key, name, hint, on, onChange) {
  const id = `sw-${key}`;
  return h("div", { class: "row" },
    h("div", { class: "text" },
      h("label", { class: "name", for: id, text: name }),
      hint && h("p", { class: "hint", id: `hint-${key}`, text: hint }),
      !on && h("span", { class: "state-off", id: `off-${key}`, text: "Off: won't wake you" })),
    h("span", { class: "switch-hit" },
      h("input", { type: "checkbox", role: "switch", id, checked: on,
                   "aria-describedby": [hint && `hint-${key}`, !on && `off-${key}`].filter(Boolean).join(" ") || false,
                   onchange: (e) => onChange(e.target.checked) })));
}

/* ---------------------------------------------------------------- Alarm rules --- */

function viewRules() {
  const out = [];
  ruleSections(config.brands).forEach((section, i) => {
    const hid = `sec-${i}`;
    out.push(h("h2", { class: "section", id: hid, text: section.title }));
    const rows = [];
    for (const row of section.rows) rows.push(...ruleRow(row));
    out.push(h("div", { class: "group", role: "group", "aria-labelledby": hid }, rows));
    if (section.footer) out.push(h("p", { class: "footer", text: section.footer }));
  });

  out.push(h("h2", { class: "section", id: "sec-display", text: "Display" }));
  out.push(h("div", { class: "group", role: "group", "aria-labelledby": "sec-display" },
    h("div", { class: "row" },
      h("div", { class: "text" },
        h("span", { class: "name", id: "units-label", text: "Temperatures in" }),
        h("p", { class: "hint", text: "Changes this page. Alarms work the same either way." })),
      h("div", { class: "segmented", role: "radiogroup", "aria-labelledby": "units-label" },
        ...["C", "F"].map((u) => h("label", {},
          h("input", { type: "radio", name: "units", value: u, checked: draft.units === u,
                       onchange: () => { draft.units = u; rerender(`units-${u}`); }, id: `units-${u}` }),
          h("span", { text: `°${u}` })))))));

  out.push(h("button", { type: "button", class: "link-button", id: "resetButton",
                         onclick: resetToRecommended, text: "Reset all alarms to recommended" }));
  return out;
}

function ruleRow(row) {
  const key = row.on || row.pct;
  const on = isOn(row, draft.rules);
  const rows = [switchRow(key, row.name, row.hint, on, (checked) => {
    if (row.on) draft.rules[row.on] = checked;
    else if (checked) draft.rules[row.pct] = lastOn[row.pct] || config.recommended[row.pct];
    else { lastOn[row.pct] = draft.rules[row.pct]; draft.rules[row.pct] = 0; }
    rerender(`sw-${key}`);
  })];
  if (!on) return rows;

  const r = config.ranges;
  if (row.minutes) {
    rows.push(...numberBox(
      { id: row.minutes, get: () => draft.rules[row.minutes], set: (n) => { draft.rules[row.minutes] = n; },
        min: r.minutes[0], max: r.minutes[1], step: 1 },
      "Ring after", "min", "minutes"));
  }
  if (row.pct) {
    rows.push(...numberBox(
      { id: row.pct, get: () => draft.rules[row.pct], set: (n) => { draft.rules[row.pct] = n; },
        min: r.percent[0], max: r.percent[1], step: 5 },
      "Ring below", "%", "percent"));
  }
  if (row.degrees) {
    rows.push(...numberBox(
      { id: row.degrees,
        get: () => diffToDisplay(draft.rules[row.degrees]),
        set: (n) => { draft.rules[row.degrees] = diffFromDisplay(n); },
        min: diffToDisplay(r.offTargetC[0]), max: diffToDisplay(r.offTargetC[1]), step: 1 },
      "Allowed difference", unitSign(), "degrees"));
  }
  return rows;
}

async function resetToRecommended() {
  const ok = await confirmSheet({
    title: "Reset to recommended?",
    body: [h("p", { text: "Every alarm goes back to its recommended setting. You can review the changes before saving." })],
    yes: "Reset", no: "Cancel", danger: false,
  });
  if (!ok) return;
  draft.rules = { ...draft.rules, ...clone(config.recommended) };
  for (const key of Object.keys(problems)) delete problems[key];
  for (const key of Object.keys(typed)) delete typed[key];
  rerender("resetButton");
}

/* --------------------------------------------------------------------- Probes --- */

function viewProbes() {
  const devices = knownDevices();
  if (devices.length === 0) {
    return [h("p", { class: "empty", text: live && live.status
      ? "No probes or pits are reporting yet." : "Waiting for the monitor to report your probes…" })];
  }
  const out = [
    h("p", { class: "footer", text:
      "Each probe uses the limits from the ETI app unless you set your own here. Your own limits are cleared when you end the cook." }),
  ];
  for (const device of devices) out.push(...deviceCard(device));
  return out;
}

function deviceCard(device) {
  const id = device.id;
  draft.devices[id] ||= normaliseDevice();
  const mine = draft.devices[id];
  const key = keyOf(id);
  const isPit = device.kind === "pit";
  const rule = isPit ? ["pit_high", "pit_low"] : ["probe_high", "probe_low"];
  const title = device.name || nameOf(id);

  const head = h("div", { class: "device-head" },
    h("h3", { id: `dev-${key}`, text: (isPit ? "Pit: " : "") + title }),
    h("span", { class: "now", "data-live": id },
      device.gone ? "Not reporting now" : liveText(device)));

  const rows = [
    h("div", { class: "row" },
      h("div", { class: "field" },
        h("label", { for: `name-${key}`, text: "Name" }),
        h("input", { type: "text", id: `name-${key}`, value: mine.name, maxlength: config.ranges.nameLength,
                     autocomplete: "off", autocapitalize: "words", enterkeyhint: "done",
                     "aria-describedby": `namehint-${key}`,
                     placeholder: "e.g. Brisket",
                     oninput: (e) => { mine.name = e.target.value; renderChrome(); } }),
        h("p", { class: "hint", id: `namehint-${key}`,
                 text: "Shown here, on the Pi's screen and in alarm messages." }))),
  ];

  const sides = isPit
    ? [["high", "Pit too hot"], ["low", "Pit too cold"]]
    : [["high", "Meat reached its target"], ["low", "Meat too cold"]];
  sides.forEach(([side, label], index) => {
    rows.push(...limitRows(device, key, mine, side, label, rule[index], isPit));
  });
  rows.push(h("p", { class: "error", id: `order-${key}`, role: "alert", hidden: !orderBad(mine) },
    h("span", { "aria-hidden": "true", text: "⚠︎" }),
    h("span", { text: "Too-cold must be below the target" })));
  return [h("section", { class: "card", "aria-labelledby": `dev-${key}` },
    head, h("div", { class: "group" }, rows))];
}

function liveText(device) {
  if (device.temp == null || device.fresh === false) return "Not reporting";
  return `${showTemp(device.temp)} now`;
}

function limitRows(device, key, mine, side, label, ruleName, isPit) {
  const choice = mine[side];
  const fieldId = `${key}-${side}`;
  const ruleOff = !draft.rules[ruleName];
  const eti = etiSeen[`${device.id}/${side}`];
  const etiWords = eti != null ? `ETI app (${showTemp(eti)})` : "ETI app";
  const r = config.ranges;
  const [lowC, highC] = isPit ? r.pitC : r.probeC;

  const choose = (mode) => {
    choice.mode = mode;
    if (mode === "set" && choice.c == null) choice.c = startingValue(eti, side, isPit);
    delete typed[`num-${fieldId}`]; delete problems[`num-${fieldId}`];
    rerender(`${fieldId}-${mode}`);
  };
  const radio = (mode, text) => h("label", {},
    h("input", { type: "radio", name: fieldId, id: `${fieldId}-${mode}`, value: mode,
                 checked: choice.mode === mode, onchange: () => choose(mode) }),
    h("span", { text }));

  const out = [
    h("div", { class: "row" },
      h("fieldset", { class: "choice" },
        h("legend", { text: label }),
        h("div", { class: "segmented" },
          radio("eti", etiWords), radio("set", "Set here"), radio("off", "Off")),
        choice.mode === "off" && h("span", { class: "state-off", text: "Off: won't wake you" }),
        ruleOff && choice.mode !== "off" && h("p", { class: "hint", text:
          `This alarm is switched off for every ${isPit ? "pit" : "probe"} on the Alarm rules page, so it won't ring.` }))),
  ];
  if (choice.mode === "set") {
    out.push(...numberBox(
      { id: fieldId, get: () => whole(toDisplay(choice.c)),
        set: (n) => { choice.c = fromDisplay(n); },
        min: Math.ceil(toDisplay(lowC)), max: Math.floor(toDisplay(highC)), step: 1 },
      "Ring at", unitSign(), "degrees"));
    if (side === "high" && !isPit) {
      out.push(h("div", { class: "sub" },
        h("span", { class: "what", id: `presets-${key}`, text: "Quick pick" }),
        h("div", { class: "chips", role: "group", "aria-labelledby": `presets-${key}` },
          config.presets.map((p) => h("button", { type: "button", class: "chip",
            id: `${fieldId}-preset-${p.name.replace(/\W+/g, "")}`,
            "data-c": String(p.c),
            "aria-pressed": String(whole(toDisplay(choice.c)) === whole(toDisplay(p.c))),
            onclick: () => { choice.c = p.c; delete typed[`num-${fieldId}`]; delete problems[`num-${fieldId}`];
                             rerender(`${fieldId}-preset-${p.name.replace(/\W+/g, "")}`); },
            text: `${p.name} ${whole(toDisplay(p.c))}°` })))));
    }
  }
  return out;
}

/* A sensible first number when you switch to "Set here". */
function startingValue(eti, side, isPit) {
  if (eti != null) return eti;
  if (isPit) return side === "high" ? 135 : 95;
  return side === "high" ? 93 : 50;
}

/* ------------------------------------------------------------------------ Now --- */

function viewNow() {
  const status = live && live.status;
  const out = [];
  if (!status) {
    out.push(h("p", { class: "empty", text: liveError
      ? "Can't reach the Pi right now." : "Waiting for the monitor…" }));
    return out;
  }
  const items = [
    ...status.pits.map((p) => ({ ...p, isPit: true })),
    ...status.probes.map((p) => ({ ...p, isPit: false })),
  ];
  out.push(h("h2", { class: "section", id: "sec-readings", text: "Readings" }));
  if (items.length === 0) out.push(h("p", { class: "empty", text: "Nothing is reporting yet." }));
  else {
    out.push(h("div", { class: "group", role: "group", "aria-labelledby": "sec-readings" },
      items.map(readingRow)));
  }
  out.push(h("p", { class: "footer", "data-age": "", text: ageText() }));

  out.push(h("div", { class: "actions-row" },
    h("button", { type: "button", class: "btn btn-secondary btn-block", id: "endCookButton",
                  onclick: endCook, text: "End cook…" }),
    h("p", { class: "hint", text:
      "Clears the targets you set here and stops waiting for probes you've taken out. Names and alarm rules stay." })));
  return out;
}

function readingRow(item) {
  const temp = item.isPit ? item.temp_c : item.core_c;
  const quiet = temp == null || item.fresh === false;
  const target = item.isPit ? null : item.target_c;
  const origin = item.isPit ? null : item.target_origin;
  const ringing = (live.status.alarms || []).some((a) => a.device === item.id && a.status === "ringing");
  return h("div", { class: "reading" },
    h("div", { class: "who" },
      h("p", { class: "name", text: (item.isPit ? "Pit: " : "") + item.name }),
      h("p", { class: "hint", text: item.source }),
      target != null && h("p", { class: "hint",
        text: `Target ${showTemp(target)}${origin === "pi" ? " (set here)" : ""}` }),
      ringing && h("span", { class: "tag alarm", text: "Alarm ringing" }),
      quiet && h("span", { class: "tag warn", text: "Not reporting" })),
    h("span", { class: quiet ? "temp stale" : "temp", text: quiet ? "–" : showTemp(temp) }));
}

function ageText() {
  if (!live || live.age_seconds == null) return "";
  return live.running
    ? `Monitor updated ${live.age_seconds} s ago.`
    : `The monitor last updated ${Math.round(live.age_seconds / 60)} min ago.`;
}

async function endCook() {
  if (changeList().length) {
    return toast("Save or discard your changes first, then end the cook.");
  }
  const mineCount = Object.values(saved.devices)
    .filter((d) => d.high.mode !== "eti" || d.low.mode !== "eti").length;
  const ok = await confirmSheet({
    title: "End this cook?",
    body: [
      h("p", { text: "This will:" }),
      h("ul", {},
        h("li", { text: mineCount
          ? `Clear the limits you set on ${mineCount} ${mineCount === 1 ? "device" : "devices"} (back to the ETI app's)`
          : "Clear any limits you've set (there are none now)" }),
        h("li", { text: "Stop waiting for probes that are no longer reporting" })),
      h("p", { text: "Names and alarm rules stay." }),
    ],
    yes: "End cook", no: "Keep cooking", danger: false,
  });
  if (!ok) return;
  const result = await safely(() => api("POST", "/api/end-cook"));
  if (!result || !result.ok) return toast("Couldn't end the cook. Try again.");
  await loadSettings({ keepDraft: false });
  await pollStatus();
  toast("Cook ended.");
}

/* ----------------------------------------------------------------- alerts bar --- */

function renderAlerts() {
  const out = [];
  const status = live && live.status;
  const ringing = status ? (status.alarms || []).filter((a) => a.status === "ringing") : [];
  if (ringing.length) {
    out.push(h("div", { class: "banner alarm", role: "alert" },
      h("span", { class: "icon", "aria-hidden": "true", text: "🔔" }),
      h("div", { class: "grow" },
        h("p", { text: ringing.length === 1 ? "An alarm is ringing" : `${ringing.length} alarms are ringing` }),
        h("ul", {}, ringing.map((a) => h("li", { text: a.message }))),
        h("button", { type: "button", class: "btn btn-secondary", id: "silenceButton",
                      onclick: silence, text: "Silence" }))));
  }
  const off = saved && config ? offAlarms(saved, knownDevices()) : new Map();
  if (off.size) {
    const names = [...off.values()];
    out.push(h("div", { class: "banner", role: "status" },
      h("span", { class: "icon", "aria-hidden": "true", text: "⚠︎" }),
      h("span", { class: "grow", text: names.length === 1
        ? `1 alarm is off: ${names[0]}.` : `${names.length} alarms are off: ${names.join(", ")}.` })));
  }
  // Only redraw when something changed, so a button inside isn't yanked away mid-tap.
  const signature = JSON.stringify([ringing.map((a) => a.message), [...off.values()]]);
  if (signature === renderAlerts.last) return;
  renderAlerts.last = signature;
  $("alerts").replaceChildren(...out);
}

async function silence() {
  const result = await safely(() => api("POST", "/api/silence"));
  toast(result && result.ok ? "Silenced. It stays quiet until the problem clears." : "Couldn't silence. Try again.");
  setTimeout(pollStatus, 1500);
}

/* The connection line, the tab dots and the Save bar. */
function renderChrome() {
  const line = $("connection");
  let text = "", bad = false;
  if (liveError) { text = "Can't reach the Pi. Showing the last readings."; bad = true; }
  else if (live && !live.running) { text = "The monitor isn't running: no alarms will ring."; bad = true; }
  else if (live) text = "Monitor running";
  if (line.textContent !== text) line.textContent = text;
  line.classList.toggle("bad", bad);

  const changes = saved ? changeList() : [];
  const dirty = changes.length > 0;
  document.querySelectorAll(".tabs a").forEach((a) => {
    const dot = a.querySelector(".dot");
    const mark = a.dataset.tab !== "now" && dirty;
    if (mark && !dot) {
      a.append(h("span", { class: "dot" },
        h("span", { "aria-hidden": "true", text: "●" }),
        h("span", { class: "visually-hidden", text: " (unsaved changes)" })));
    }
    if (!mark && dot) dot.remove();
  });
  $("savebar").hidden = !dirty;
  $("changeCount").textContent = changes.length === 1 ? "1 unsaved change" : `${changes.length} unsaved changes`;
  $("saveButton").disabled = hasProblems() || saving;
  renderAlerts();
}

/* ------------------------------------------------------------------- saving --- */

function payload() {
  const devices = {};
  for (const [id, d] of Object.entries(draft.devices)) {
    const one = {};
    if (d.name.trim()) one.name = d.name.trim();
    for (const side of ["high", "low"]) {
      if (d[side].mode === "set") one[side] = { mode: "set", c: d[side].c };
      else if (d[side].mode === "off") one[side] = { mode: "off" };
    }
    if (Object.keys(one).length) devices[id] = one;
  }
  return { revision, alarms: draft.rules, units: draft.units, devices };
}

async function save() {
  if (hasProblems() || saving) return;
  // Switching an alarm off is the one change that can cost you a cook, so it is
  // the one that asks first. Everything else saves straight away.
  const devices = knownDevices();
  const before = offAlarms(saved, devices), after = offAlarms(draft, devices);
  const newlyOff = [...after].filter(([key]) => !before.has(key)).map(([, words]) => words);
  if (newlyOff.length) {
    const ok = await confirmSheet({
      title: newlyOff.length === 1 ? "Turn this alarm off?" : `Turn ${newlyOff.length} alarms off?`,
      body: [h("p", { text: "These won't wake you until you turn them back on:" }),
             h("ul", {}, newlyOff.map((n) => h("li", { text: n })))],
      yes: "Turn off and save", no: newlyOff.length === 1 ? "Keep it on" : "Keep them on", danger: true,
    });
    if (!ok) return;
  }
  saving = true;
  $("saveButton").textContent = "Saving…";
  renderChrome();
  const result = await safely(() => api("PUT", "/api/settings", payload()));
  saving = false;
  $("saveButton").textContent = "Save";
  if (!result) { renderChrome(); return toast("Couldn't reach the Pi. Your changes are still here: try again."); }
  if (result.status === 409) return conflict(result.data);
  if (result.status === 422) { renderChrome(); return toast(firstProblem(result.data)); }
  if (!result.ok) { renderChrome(); return toast((result.data && result.data.error) || "Couldn't save. Try again."); }
  adopt(result.data);
  rerender();
  toast("Saved. The monitor uses it within a second or two.");
}

function firstProblem(data) {
  const errors = (data && data.errors) || {};
  const [field, message] = Object.entries(errors)[0] || [];
  return field ? `Couldn't save: ${message}.` : "Couldn't save. Check the numbers and try again.";
}

/* Someone (another phone) saved since this page loaded. Never overwrite silently. */
async function conflict(data) {
  const keepMine = await confirmSheet({
    title: "Settings changed elsewhere",
    body: [h("p", { text: "Someone saved different settings since you opened this page. Your changes weren't saved." })],
    yes: "Keep my changes and review", no: "Load the latest", danger: false,
  });
  if (data && data.rules) {
    const mine = clone(draft);
    adopt(data);
    if (keepMine) { draft = mine; }
  }
  rerender();
}

function discard() {
  draft = clone(saved);
  for (const key of Object.keys(problems)) delete problems[key];
  for (const key of Object.keys(typed)) delete typed[key];
  rerender();
  toast("Changes discarded");
}

/* ------------------------------------------------------------- confirm sheet --- */

function confirmSheet({ title, body, yes, no, danger }) {
  return new Promise((resolve) => {
    $("confirmTitle").textContent = title;
    $("confirmBody").replaceChildren(...body);
    const yesButton = $("confirmYes");
    yesButton.textContent = yes;
    yesButton.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    $("confirmNo").textContent = no;
    const dialog = $("confirm");
    const done = (answer) => { dialog.close(); resolve(answer); };
    yesButton.onclick = () => done(true);
    $("confirmNo").onclick = () => done(false);
    dialog.oncancel = (e) => { e.preventDefault(); done(false); };
    dialog.showModal();
  });
}

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 5000);
}

async function safely(call) {
  try { return await call(); } catch { return null; }
}

/* ----------------------------------------------------------- loading and polling --- */

async function loadSettings({ keepDraft = false } = {}) {
  const result = await safely(() => api("GET", "/api/settings"));
  if (!result) throw new Error("unreachable");
  if (!result.ok) throw new Error((result.data && result.data.error) || "The Pi couldn't load the settings.");
  const mine = keepDraft && draft ? clone(draft) : null;
  adopt(result.data);
  if (mine) draft = mine;
}

async function pollStatus() {
  const result = await safely(() => api("GET", "/api/status"));
  const before = JSON.stringify(deviceIdsAndNames());
  if (result && result.ok) { live = result.data; liveError = false; }
  else liveError = true;
  if (!config) return;
  const structural = before !== JSON.stringify(deviceIdsAndNames());
  const editing = document.activeElement && $("main").contains(document.activeElement)
    && document.activeElement.matches("input[type=text]");
  if (currentTab === "now" || (structural && !editing)) renderView(currentTab);
  else updateLiveBits();
  renderChrome();
}

function deviceIdsAndNames() {
  return live && live.status
    ? [live.status.pits.map((p) => p.id), live.status.probes.map((p) => [p.id, p.name])] : [];
}

/* Update temperatures in place, so typing in the Probes page isn't interrupted. */
function updateLiveBits() {
  const devices = knownDevices();
  document.querySelectorAll("[data-live]").forEach((el) => {
    const d = devices.find((x) => x.id === el.dataset.live);
    if (d) el.textContent = d.gone ? "Not reporting now" : liveText(d);
  });
}

let timer = null;
function startPolling() {
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) pollStatus(); }, STATUS_EVERY_MS);
}

/* --------------------------------------------------------------------- routing --- */

const TITLES = { now: "Now", probes: "Probes", rules: "Alarm rules" };

function route() {
  const tab = (location.hash || "#now").slice(1);
  currentTab = TITLES[tab] ? tab : "now";
  $("title").textContent = TITLES[currentTab];
  document.title = `${TITLES[currentTab]} · Smoker`;
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.tab === currentTab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  for (const name of Object.keys(TITLES)) $(`view-${name}`).hidden = name !== currentTab;
  if (config) {
    renderView(currentTab);
    renderChrome();
  }
}

/* ----------------------------------------------------------------------- start --- */

async function start() {
  $("saveButton").addEventListener("click", save);
  $("discardButton").addEventListener("click", discard);
  window.addEventListener("hashchange", () => { route(); $("main").focus({ preventScroll: true }); });
  window.addEventListener("beforeunload", (e) => { if (saved && changeList().length) e.preventDefault(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });
  route();
  try {
    await loadSettings();
  } catch (e) {
    $("loading").textContent = e.message === "unreachable"
      ? "Can't reach the Pi. Check you're on your home Wi-Fi, then reload."
      : e.message;
    return;
  }
  $("loading").hidden = true;
  await pollStatus();
  route();
  startPolling();
}

start();
