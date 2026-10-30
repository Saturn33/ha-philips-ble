// The card file registers its elements once the frontend is there: node --test tests/card/*.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

test("the card file defines the element and the editor", () => {
  const defined = new Map();
  globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = { innerHTML: "" }; return this.shadowRoot; } };
  globalThis.window = {
    customElements: {
      get: (name) => (name === "home-assistant" ? class {} : defined.get(name)),
      define: (name, element) => defined.set(name, element),
    },
  };
  const require = createRequire(import.meta.url);
  require("../../custom_components/ble_mesh_lights/frontend/ble-mesh-remote-card.js");
  assert.ok(defined.has("ble-mesh-remote-card"));
  assert.ok(defined.has("ble-mesh-remote-card-editor"));
  const cards = window.customCards.filter((card) => card.type === "ble-mesh-remote-card");
  assert.equal(cards.length, 1);
  assert.equal(cards[0].name, "BLE Mesh remote");
  assert.equal(cards[0].preview, true); // the card picker draws a live preview
  const Card = defined.get("ble-mesh-remote-card");
  assert.equal(typeof Card.getConfigElement, "function");
  assert.equal(typeof Card.getStubConfig, "function");
  // The default size in a sections view must fit the layout (4 of 12 columns cut the vertical one).
  const size = (layout) => {
    const card = new Card();
    card.setConfig({ entities: ["light.a"], layout });
    return card.getGridOptions();
  };
  assert.deepEqual(size("vertical"), { columns: 6, min_columns: 6 });
  assert.deepEqual(size("mini"), { columns: 6, min_columns: 6 });
  assert.deepEqual(size("horizontal"), { columns: 12, min_columns: 9 });
});
