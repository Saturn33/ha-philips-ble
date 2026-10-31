// Node tests for the pure model of the remote card: node --test tests/card/*.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { RemoteModel: M } = require("../../custom_components/ble_mesh_lights/frontend/ble-mesh-remote-card.js");

const LEVELS = [100, 80, 50, 30, 10, 5];
const LABELS = { relax: "Relax", bright: "Bright", on: "On" };
const info = (profile = "valen") => ({ profile, brightness_levels: LEVELS, labels: LABELS, commands: ["on", "relax"] });
const lamp = (state, attrs = {}) => ({
  state,
  attributes: { friendly_name: "Street", color_temp_kelvin: 2700, brightness: 128, effect: null, state_uncertain: false, ...attrs },
});
const view = (id, stateObj, i = info(), loaded = true) => M.lampView(id, stateObj, i, loaded);

test("lampView maps brightness to the nearest level", () => {
  assert.equal(view("light.a", lamp("on", { brightness: 128 })).level, 50);
  assert.equal(view("light.a", lamp("on", { brightness: 26 })).level, 10);
  assert.equal(view("light.a", lamp("on", { brightness: 13 })).level, 5);
  assert.equal(view("light.a", lamp("on", { brightness: null })).level, null);
});

test("lampView takes the scene from effect only", () => {
  assert.equal(view("light.a", lamp("on", { effect: null })).scene, null);
  assert.equal(view("light.a", lamp("on", { effect: "relax" })).scene, "relax");
});

test("lampView: unknown counts as off and uncertain; name falls back to the id", () => {
  const v = view("light.a", { state: "unknown", attributes: {} });
  assert.equal(v.on, false);
  assert.equal(v.uncertain, true);
  assert.equal(v.name, "light.a");
});

test("cardView shows one lamp", () => {
  const c = M.cardView([view("light.a", lamp("on", { effect: "relax" }))], { lang: "en" });
  assert.equal(c.title, "Street");
  assert.equal(c.line, "2700 K · 50 %");
  assert.equal(c.sceneLine, "Relax");
  assert.equal(c.highlight, "relax");
  assert.deepEqual(c.bulb, { kelvin: 2700, level: 50 });
  assert.equal(c.disabled, false);
  assert.equal(M.cardView([view("light.a", lamp("on"))], { lang: "en", name: "Hall" }).title, "Hall");
});

test("cardView marks uncertain apart from the line", () => {
  // The card draws "?" as its own element with a hint, so the line itself has none.
  const c = M.cardView([view("light.a", lamp("on", { state_uncertain: true }))], { lang: "en" });
  assert.equal(c.line, "2700 K · 50 %");
  assert.equal(c.uncertain, true);
  assert.equal(c.uncertainHint, "State uncertain: the light may look different (a missed remote press or a switch at the wall)");
  const unknown = M.cardView([view("light.a", lamp("on", { brightness: null }))], { lang: "ru" });
  assert.equal(unknown.line, "2700 K · — %");
  assert.equal(unknown.uncertain, true);
  assert.match(unknown.uncertainHint, /^Состояние под вопросом/);
  assert.equal(M.cardView([view("light.a", lamp("on"))], { lang: "en" }).uncertain, false);
  assert.equal(M.cardView([view("light.a", lamp("on")), view("light.b", lamp("on", { state_uncertain: true }))], { lang: "en" }).uncertain, false);
});

test("cardView: off and no power for one lamp", () => {
  assert.equal(M.cardView([view("light.a", lamp("off"))], { lang: "en" }).line, "Off");
  assert.equal(M.cardView([view("light.a", lamp("off"))], { lang: "ru" }).line, "Выключен");
  const gone = M.cardView([view("light.a", lamp("unavailable"))], { lang: "ru" });
  assert.equal(gone.line, "Нет питания");
  assert.equal(gone.disabled, true);
});

test("cardView summarises several lamps", () => {
  const on = (id) => view(id, lamp("on"));
  const off = (id) => view(id, lamp("off"));
  const gone = (id) => view(id, lamp("unavailable"));
  const line = (views, lang = "en") => M.cardView(views, { lang }).line;
  // All lamps in one state: "All …".
  assert.equal(line([on("a"), on("b")]), "All on");
  assert.equal(line([off("a"), off("b")], "ru"), "Все выключены");
  assert.equal(line([gone("a"), gone("b")]), "All without power");
  assert.equal(line([gone("a"), gone("b")], "ru"), "Все без питания");
  // All with power, some off: "N of M on".
  assert.equal(line([on("a"), on("b"), off("c"), off("d"), off("e")]), "2 of 5 on");
  assert.equal(line([on("a"), on("b"), off("c"), off("d"), off("e")], "ru"), "2 из 5 включены");
  // Some without power: separate counts, zero ones left out.
  assert.equal(line([on("a"), on("b"), gone("c"), gone("d"), gone("e")]), "2 on · 3 without power");
  assert.equal(line([on("a"), on("b"), gone("c"), gone("d"), gone("e")], "ru"), "2 включены · 3 без питания");
  assert.equal(line([on("a"), off("b"), gone("c"), gone("d"), gone("e")]), "1 on · 1 off · 3 without power");
  assert.equal(line([on("a"), off("b"), gone("c"), gone("d"), gone("e")], "ru"), "1 включена · 1 выключена · 3 без питания");
  assert.equal(line([off("a"), off("b"), gone("c")], "ru"), "2 выключены · 1 без питания");
  assert.equal(line([on("a"), on("b"), on("c"), on("d"), on("e"), gone("f")], "ru"), "5 включены · 1 без питания");
  assert.equal(M.cardView([on("a"), off("b"), off("c"), gone("d")], { lang: "en" }).title, "4 lamps");
});

test("cardView highlights a shared scene only", () => {
  const relax = view("light.a", lamp("on", { effect: "relax" }));
  const other = view("light.b", lamp("on", { effect: "bright", color_temp_kelvin: 3000, brightness: 255 }));
  const off = view("light.c", lamp("off"));
  assert.equal(M.cardView([relax, off], { lang: "en" }).highlight, "relax");
  assert.equal(M.cardView([relax, other], { lang: "en" }).highlight, null);
});

test("cardView disables only when all are unavailable", () => {
  const gone = view("light.a", lamp("unavailable"));
  assert.equal(M.cardView([gone, view("light.b", lamp("off"))], { lang: "en" }).disabled, false);
  assert.equal(M.cardView([gone, view("light.b", lamp("unavailable"))], { lang: "en" }).disabled, true);
});

test("cardView tells loading from missing", () => {
  const loading = M.cardView([view("light.a", lamp("on"), null, false)], { lang: "en" });
  assert.equal(loading.loading, true);
  assert.equal(loading.error, null);
  const missing = M.cardView([view("light.gone", undefined)], { lang: "en" });
  assert.equal(missing.error, "Lamp not found: light.gone");
});

test("cardView reports a non-lamp entity", () => {
  const c = M.cardView([view("light.strip", lamp("on"), null, true)], { lang: "ru" });
  assert.equal(c.loading, false);
  assert.equal(c.error, "Не лампа BLE Mesh: light.strip");
});

test("cardView refuses mixed profiles", () => {
  const c = M.cardView([view("light.a", lamp("on")), view("light.b", lamp("on"), info("skylight"))], { lang: "en" });
  assert.equal(c.error, "Lamps of different types");
});

test("steps fill to the level", () => {
  const filled = (brightness, state = "on") => M.cardView([view("light.a", lamp(state, { brightness }))], { lang: "en" }).steps;
  assert.deepEqual(filled(255), { count: 6, filled: 6 });
  assert.deepEqual(filled(128), { count: 6, filled: 4 });
  assert.deepEqual(filled(13), { count: 6, filled: 1 });
  assert.deepEqual(filled(128, "off"), { count: 6, filled: 0 });
});

test("ledColor", () => {
  const ids = ["light.a", "light.b"];
  assert.equal(M.ledColor(ids, { "light.a": { sent: true }, "light.b": { sent: true } }), "green");
  assert.equal(M.ledColor(ids, { "light.a": { sent: true } }), "yellow");
  assert.equal(M.ledColor(ids, { "light.a": { sent: true }, "light.b": { sent: false, reason: "failed" } }), "yellow");
  assert.equal(M.ledColor(ids, { "light.a": { sent: false }, "light.b": { sent: false } }), "red");
  assert.equal(M.ledColor(ids, null), "red");
});

test("ledMessage", () => {
  const ids = ["light.a", "light.b", "light.c"];
  const names = { "light.a": "Street", "light.b": "Patio", "light.c": "Hall" };
  const response = {
    "light.a": { sent: true },
    "light.b": { sent: false, reason: "queue_full", error: "Too many" },
  };
  assert.equal(M.ledMessage(ids, response, { names, lang: "en" }), "Not sent: Patio (too many presses), Hall (no power)");
  assert.equal(M.ledMessage(ids, response, { names, lang: "ru" }), "Не отправлено: Patio (слишком много нажатий), Hall (нет питания)");
  const failed = { "light.a": { sent: false, reason: "failed", error: "action_call_failed" } };
  assert.equal(M.ledMessage(["light.a"], failed, { names, lang: "en" }), "Not sent: Street (action_call_failed)");
  assert.equal(M.ledMessage(["light.a"], null, { names, lang: "en", error: "Boom" }), "Not sent: Boom");
  assert.equal(M.ledMessage(["light.a"], { "light.a": { sent: true } }, { names, lang: "en" }), "");
});

test("pickLayout", () => {
  // No adaptive layout (owner, live check): vertical by default; an old "adaptive" config draws vertical.
  assert.equal(M.pickLayout(undefined, 380), "vertical");
  assert.equal(M.pickLayout("adaptive", 380), "vertical");
  assert.equal(M.pickLayout("vertical", 380), "vertical");
  assert.equal(M.pickLayout("mini", 200), "mini");
  assert.equal(M.pickLayout("horizontal", 450), "horizontal");
  // Live check: the horizontal layout falls apart below a 436 px wide card (a 451 px window).
  assert.equal(M.pickLayout("horizontal", 436), "horizontal");
  assert.equal(M.pickLayout("horizontal", 435), "mini");
  assert.equal(M.pickLayout("horizontal", 300), "mini");
  assert.equal(M.pickLayout("horizontal", 220), "vertical");
});

test("the layout options have no adaptive", () => {
  assert.deepEqual(M.LAYOUT_OPTIONS, ["vertical", "horizontal", "mini"]);
});

test("lampsTitle in Russian", () => {
  assert.deepEqual(
    [1, 2, 5, 11, 21, 22].map((n) => M.lampsTitle(n, "ru")),
    ["1 лампа", "2 лампы", "5 ламп", "11 ламп", "21 лампа", "22 лампы"],
  );
  assert.equal(M.lampsTitle(3, "en"), "3 lamps");
});

test("fallbackRemote lists the profile commands", () => {
  const remote = M.fallbackRemote(["on", "off", "relax"]);
  assert.deepEqual(remote.groups, { all: { kind: "row", wrap: true, buttons: ["on", "off", "relax"] } });
  assert.deepEqual(remote.layouts, { vertical: [["all"]], horizontal: [["all"]], mini: [["all"]] });
  assert.deepEqual(remote.buttons, {});
});

test("lampLines lists every lamp with its own line", () => {
  const views = [
    view("light.a", lamp("on", { friendly_name: "Street", state_uncertain: true })),
    view("light.b", lamp("off", { friendly_name: "Patio" })),
    view("light.c", lamp("unavailable", { friendly_name: "Hall" })),
  ];
  assert.deepEqual(M.lampLines(views, "ru"), [
    { id: "light.a", name: "Street", line: "2700 K · 50 %", uncertain: true },
    { id: "light.b", name: "Patio", line: "Выключен", uncertain: false },
    { id: "light.c", name: "Hall", line: "Нет питания", uncertain: false },
  ]);
});

test("cardView: a missing state while loading is loading", () => {
  // At HA start-up the entity states may come after the card: not "Lamp not found" yet.
  const c = M.cardView([M.lampView("light.a", undefined, null, false)], { lang: "en" });
  assert.equal(c.loading, true);
  assert.equal(c.error, null);
});

test("cardView reports a lamp whose profile failed", () => {
  const c = M.cardView([M.lampView("light.a", lamp("unavailable"), null, true, "valen")], { lang: "ru" });
  assert.equal(c.error, "Профиль не загрузился: valen");
});

test("fallbackRemote mini wraps", () => {
  assert.equal(M.fallbackRemote(["on", "off"]).groups.all.wrap, true);
});

test("the card speaks Spanish", () => {
  const on = (id) => view(id, lamp("on"));
  const off = (id) => view(id, lamp("off"));
  const gone = (id) => view(id, lamp("unavailable"));
  assert.equal(M.cardView([off("a")], { lang: "es" }).line, "Apagada");
  assert.equal(M.cardView([gone("a")], { lang: "es" }).line, "Sin corriente");
  assert.equal(M.cardView([on("a"), on("b"), off("c")], { lang: "es" }).line, "2 de 3 encendidas");
  assert.equal(M.cardView([on("a"), off("b"), gone("c")], { lang: "es" }).line, "1 encendida · 1 apagada · 1 sin corriente");
  assert.equal(M.cardView([on("a"), on("b")], { lang: "es" }).line, "Todas encendidas");
  assert.deepEqual([1, 2, 5].map((n) => M.lampsTitle(n, "es")), ["1 lámpara", "2 lámparas", "5 lámparas"]);
  assert.equal(M.ledMessage(["light.a"], {}, { names: { "light.a": "Calle" }, lang: "es" }), "No enviado: Calle (sin corriente)");
  assert.equal(M.cardView([on("a")], { lang: "es-ES" }).uncertainHint.startsWith("Estado dudoso"), true);
});
