// The card element against a minimal DOM stub: node --test tests/card/*.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

/** A shadow root that counts full rebuilds and hands out stable fake elements by selector. */
function fakeShadow() {
  const elements = new Map();
  const element = (key) => {
    if (!elements.has(key)) {
      const classes = new Set();
      elements.set(key, {
        textContent: "",
        style: {},
        disabled: false,
        dataset: {},
        classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)), contains: (n) => classes.has(n) },
        addEventListener() {},
        set className(value) { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c)); },
        get className() { return [...classes].join(" "); },
      });
    }
    return elements.get(key);
  };
  let html = "";
  return {
    builds: 0,
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; this.builds += 1; elements.clear(); },
    querySelector: (selector) => element(selector),
    querySelectorAll: () => [],
    element,
  };
}

const defined = new Map();
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = fakeShadow(); return this.shadowRoot; }
  dispatchEvent() {}
};
globalThis.window = {
  innerWidth: 1200,
  customElements: { get: (n) => (n === "home-assistant" ? class {} : defined.get(n)), define: (n, e) => defined.set(n, e) },
};
createRequire(import.meta.url)("../../custom_components/ble_mesh_lights/frontend/ble-mesh-remote-card.js");
const Card = defined.get("ble-mesh-remote-card");

const INFO = { profile: "valen", commands: ["on", "off"], brightness_levels: [100, 80, 50, 30, 10, 5], remote: null, labels: {} };
const lampState = (brightness) => ({ state: "on", attributes: { friendly_name: "Street", color_temp_kelvin: 2700, brightness } });

function makeCard(callWS) {
  const card = new Card();
  card.clientWidth = 300;
  card.setConfig({ entities: ["light.a"] });
  card.connectedCallback();
  return card;
}

test("a card shown again after a failed start fetches again", async () => {
  let calls = 0;
  let ready = false;
  const hass = {
    language: "en",
    states: { "light.a": lampState(128) },
    callWS: async () => {
      calls += 1;
      if (!ready) throw new Error("unknown_command");
      return { loaded: true, lamps: { "light.a": INFO } };
    },
  };
  const card = makeCard();
  card.hass = hass;
  await tick();
  assert.equal(calls, 1);
  card.disconnectedCallback(); // the view is switched away while HA still starts
  ready = true;
  card.connectedCallback(); // shown again
  card.hass = { ...hass };
  await tick();
  assert.equal(calls, 2);
  assert.equal(card._info.loaded, true);
  card.disconnectedCallback();
});

test("a detached card does not keep retrying", async () => {
  let release;
  const hass = {
    language: "en",
    states: { "light.a": lampState(128) },
    callWS: () => new Promise((_, reject) => { release = () => reject(new Error("unknown_command")); }),
  };
  const card = makeCard();
  card.hass = hass;
  card.disconnectedCallback(); // detached while the fetch is in flight
  release();
  await tick();
  assert.equal(card._retryTimer, null);
});

test("a state change patches the card instead of rebuilding it", async () => {
  const hass = { language: "en", states: { "light.a": lampState(128) }, callWS: async () => ({ loaded: true, lamps: { "light.a": INFO } }) };
  const card = makeCard();
  card.hass = hass;
  await tick();
  const builds = card.shadowRoot.builds;
  assert.equal(card.shadowRoot.element(".line .value").textContent, "2700 K · 50 %");
  card.hass = { ...hass, states: { "light.a": lampState(255) } }; // a press raised the level
  assert.equal(card.shadowRoot.builds, builds); // buttons and LED stay the same elements
  assert.equal(card.shadowRoot.element(".line .value").textContent, "2700 K · 100 %");
  card.disconnectedCallback();
});

test("the header opens the lamp, or the list of a card's lamps", async () => {
  const info = { loaded: true, lamps: { "light.a": INFO, "light.b": INFO } };
  const hass = {
    language: "en",
    states: { "light.a": lampState(128), "light.b": { state: "off", attributes: { friendly_name: "Patio" } } },
    callWS: async () => info,
  };
  const events = [];
  const one = makeCard();
  one.dispatchEvent = (event) => events.push(event);
  one.hass = hass;
  await tick();
  one._headerTapped();
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "hass-more-info");
  assert.deepEqual(events[0].detail, { entityId: "light.a" });
  one.disconnectedCallback();

  const several = new Card();
  several.clientWidth = 300;
  several.setConfig({ entities: ["light.a", "light.b"] });
  several.connectedCallback();
  several.dispatchEvent = (event) => events.push(event);
  several.hass = hass;
  await tick();
  assert.ok(!several.shadowRoot.innerHTML.includes('class="lamps"'));
  several._headerTapped(); // opens the list instead of a lamp
  assert.equal(events.length, 1);
  assert.ok(several.shadowRoot.innerHTML.includes('class="lamps"'));
  assert.ok(several.shadowRoot.innerHTML.includes('data-entity="light.b"'));
  several._lampTapped("light.b");
  assert.deepEqual(events[1].detail, { entityId: "light.b" });
  several._headerTapped(); // closes it
  assert.ok(!several.shadowRoot.innerHTML.includes('class="lamps"'));
  several.disconnectedCallback();
});

test("a card without lamps shows a picture of the remote instead of failing", () => {
  const card = new Card();
  card.setConfig({ entities: [] }); // the picker's preview when there is no lamp yet
  card.hass = { language: "ru", states: {}, callWS: async () => ({ loaded: true, lamps: {} }) };
  const html = card.shadowRoot.innerHTML;
  assert.ok(html.includes('class="placeholder"'));
  assert.ok(html.includes("<svg"));
  assert.ok(html.includes("Выберите светильники"));
  card.connectedCallback(); // resizes and reconnects leave the picture alone
  card._resized();
  assert.ok(card.shadowRoot.innerHTML.includes('class="placeholder"'));
  card.disconnectedCallback();
});

test("a key change during a fetch fetches again", async () => {
  let release;
  const asked = [];
  const hass = {
    language: "en",
    states: { "light.a": lampState(128), "light.b": lampState(128) },
    callWS: (message) => {
      asked.push(message.entity_ids.join(","));
      return asked.length === 1
        ? new Promise((resolve) => { release = () => resolve({ loaded: true, lamps: { "light.a": INFO }, failed: {} }); })
        : Promise.resolve({ loaded: true, lamps: { "light.a": INFO, "light.b": INFO }, failed: {} });
    },
  };
  const card = makeCard();
  card.hass = hass;
  card.setConfig({ entities: ["light.a", "light.b"] }); // the lamps change while the first fetch runs
  release();
  await tick();
  await tick();
  assert.deepEqual(asked, ["light.a", "light.a,light.b"]);
  assert.ok(card._info.lamps["light.b"]);
  card.disconnectedCallback();
});

test("a press with every lamp unavailable says no power", async () => {
  const messages = [];
  const hass = {
    language: "en",
    states: { "light.a": { state: "unavailable", attributes: { friendly_name: "Street" } } },
    callWS: async () => ({ loaded: true, lamps: { "light.a": INFO }, failed: {} }),
  };
  const card = makeCard();
  card.dispatchEvent = (event) => messages.push(event);
  card.hass = hass;
  await tick();
  await card._press("on");
  const note = messages.find((event) => event.type === "hass-notification");
  assert.equal(note.detail.message, "Not sent: Street (no power)");
  card.disconnectedCallback();
});

test("an answer for the old lamps is not shown for the new ones", async () => {
  let releaseFirst;
  let releaseSecond;
  const hass = {
    language: "en",
    states: { "light.a": lampState(128), "light.b": lampState(128) },
    callWS: (message) =>
      message.entity_ids.length === 1
        ? new Promise((resolve) => { releaseFirst = () => resolve({ loaded: true, lamps: { "light.a": INFO }, failed: {} }); })
        : new Promise((resolve) => { releaseSecond = () => resolve({ loaded: true, lamps: { "light.a": INFO, "light.b": INFO }, failed: {} }); }),
  };
  const card = makeCard();
  card.hass = hass;
  card.setConfig({ entities: ["light.a", "light.b"] });
  releaseFirst(); // the answer for light.a alone arrives while the lamps already changed
  await tick();
  assert.equal(card._info, null); // not kept: light.b would read "Not a BLE Mesh lamp"
  releaseSecond();
  await tick();
  assert.ok(card._info.lamps["light.b"]);
  card.disconnectedCallback();
});
