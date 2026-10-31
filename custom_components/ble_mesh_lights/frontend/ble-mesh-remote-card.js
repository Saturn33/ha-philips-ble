/*
 * BLE Mesh Lights virtual remote (Lovelace card).
 *
 *   type: custom:ble-mesh-remote-card
 *   entities: [light.street, light.patio]   # lamps of one profile
 *   name: Whole flat                          # optional
 *   layout: vertical                          # vertical | horizontal | mini
 *   hide_header: false
 *
 * Looks like the lamps' remote (the profile's remote.json, read through the
 * ble_mesh_lights/lamps websocket command) and sends each press through
 * ble_mesh_lights.send_command with queue: true, so every press reaches the lamps.
 * The state shown is the lamps' state in Home Assistant; the card computes nothing.
 */

const VERTICAL_MIN = 200;
const LAYOUT_OPTIONS = ["vertical", "horizontal", "mini"];
const MINI_MIN = 240;
const HORIZONTAL_MIN = 436; // card width; below it the horizontal layout falls apart (live check)

const STRINGS = {
  en: {
    off: "Off",
    noPower: "No power",
    allOn: "All on",
    allOff: "All off",
    allWithoutPower: "All without power",
    someOn: (on, of) => `${on} of ${of} on`,
    countOn: (n) => `${n} on`,
    countOff: (n) => `${n} off`,
    withoutPower: (n) => `${n} without power`,
    lamps: (n) => `${n} lamps`,
    loading: "Loading…",
    chooseLamps: "Choose the lights in the card's settings",
    uncertain: "State uncertain: the light may look different (a missed remote press or a switch at the wall)",
    notFound: (id) => `Lamp not found: ${id}`,
    notLamp: (id) => `Not a BLE Mesh lamp: ${id}`,
    profileFailed: (profile) => `Profile failed to load: ${profile}`,
    mixed: "Lamps of different types",
    notSent: "Not sent",
    reasons: {
      queue_full: "too many presses",
      no_board: "no board hears it",
      reloaded: "the integration is restarting",
      absent: "no power",
    },
    editor: {
      entities: "Lamps",
      name: "Name",
      layout: "Layout",
      hide_header: "Hide header",
      layouts: { vertical: "Vertical", horizontal: "Horizontal", mini: "Mini" },
    },
  },
  ru: {
    off: "Выключен",
    noPower: "Нет питания",
    allOn: "Все включены",
    allOff: "Все выключены",
    allWithoutPower: "Все без питания",
    someOn: (on, of) => `${on} из ${of} включены`,
    countOn: (n) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "включена" : "включены"}`,
    countOff: (n) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "выключена" : "выключены"}`,
    withoutPower: (n) => `${n} без питания`,
    lamps: (n) => `${n} ${pluralRu(n, "лампа", "лампы", "ламп")}`,
    loading: "Загрузка…",
    chooseLamps: "Выберите светильники в настройках карточки",
    uncertain: "Состояние под вопросом: светильник может выглядеть иначе (пропущено нажатие пульта или щёлкнули выключателем)",
    notFound: (id) => `Лампа не найдена: ${id}`,
    notLamp: (id) => `Не лампа BLE Mesh: ${id}`,
    profileFailed: (profile) => `Профиль не загрузился: ${profile}`,
    mixed: "Лампы разных типов",
    notSent: "Не отправлено",
    reasons: {
      queue_full: "слишком много нажатий",
      no_board: "её не слышит ни одна плата",
      reloaded: "интеграция перезапускается",
      absent: "нет питания",
    },
    editor: {
      entities: "Светильники",
      name: "Название",
      layout: "Раскладка",
      hide_header: "Скрыть шапку",
      layouts: { vertical: "Вертикальный", horizontal: "Горизонтальный", mini: "Мини" },
    },
  },
  es: {
    off: "Apagada",
    noPower: "Sin corriente",
    allOn: "Todas encendidas",
    allOff: "Todas apagadas",
    allWithoutPower: "Todas sin corriente",
    someOn: (on, of) => `${on} de ${of} encendidas`,
    countOn: (n) => `${n} ${n === 1 ? "encendida" : "encendidas"}`,
    countOff: (n) => `${n} ${n === 1 ? "apagada" : "apagadas"}`,
    withoutPower: (n) => `${n} sin corriente`,
    lamps: (n) => `${n} ${n === 1 ? "lámpara" : "lámparas"}`,
    loading: "Cargando…",
    chooseLamps: "Elige las luces en los ajustes de la tarjeta",
    uncertain: "Estado dudoso: la luz puede verse distinta (una pulsación del mando perdida o un cambio en el interruptor)",
    notFound: (id) => `Luz no encontrada: ${id}`,
    notLamp: (id) => `No es una luz BLE Mesh: ${id}`,
    profileFailed: (profile) => `No se ha podido cargar el perfil: ${profile}`,
    mixed: "Luces de tipos distintos",
    notSent: "No enviado",
    reasons: {
      queue_full: "demasiadas pulsaciones",
      no_board: "ninguna placa la oye",
      reloaded: "la integración se está reiniciando",
      absent: "sin corriente",
    },
    editor: {
      entities: "Luces",
      name: "Nombre",
      layout: "Disposición",
      hide_header: "Ocultar la cabecera",
      layouts: { vertical: "Vertical", horizontal: "Horizontal", mini: "Mini" },
    },
  },
};

function pluralRu(n, one, few, many) {
  const tens = n % 100;
  const units = n % 10;
  if (tens >= 11 && tens <= 14) return many;
  if (units === 1) return one;
  if (units >= 2 && units <= 4) return few;
  return many;
}

const strings = (lang) => STRINGS[String(lang || "en").split("-")[0]] || STRINGS.en;

const nearest = (values, target) =>
  values.reduce((best, value) => (Math.abs(value - target) < Math.abs(best - target) ? value : best), values[0]);

/** One lamp's status line: values, "Off" or "No power", and whether it is uncertain. */
function lampLine(v, t) {
  if (!v.available) return { line: t.noPower, uncertain: false };
  if (!v.on) return { line: t.off, uncertain: false };
  return { line: `${v.kelvin ?? "—"} K · ${v.level ?? "—"} %`, uncertain: v.uncertain };
}

const RemoteModel = {
  LAYOUT_OPTIONS,

  /** Each lamp of a several-lamp card, for the list the header opens. */
  lampLines(views, lang) {
    const t = strings(lang);
    return views.map((v) => ({ id: v.id, name: v.name, ...lampLine(v, t) }));
  },

  /** One lamp as the card needs it, from its HA state and the integration's info about it. */
  lampView(entityId, stateObj, info, loaded, failedProfile = null) {
    const attrs = (stateObj && stateObj.attributes) || {};
    const levels = (info && info.brightness_levels) || [];
    const on = !!stateObj && stateObj.state === "on";
    const kelvin = on && attrs.color_temp_kelvin != null ? attrs.color_temp_kelvin : null;
    const level =
      on && attrs.brightness != null && levels.length ? nearest(levels, (attrs.brightness * 100) / 255) : null;
    return {
      id: entityId,
      name: attrs.friendly_name || entityId,
      profile: info ? info.profile : null,
      levels,
      labels: (info && info.labels) || {},
      // Until the integration answers, a missing state may just not be there yet (HA start-up).
      missing: !stateObj && !!loaded && !failedProfile,
      loading: !info && !loaded,
      failedProfile: !info && loaded ? failedProfile : null,
      notLamp: !!stateObj && !info && !!loaded && !failedProfile,
      available: !!stateObj && stateObj.state !== "unavailable",
      on,
      kelvin,
      level,
      scene: on ? attrs.effect || null : null,
      uncertain:
        !!attrs.state_uncertain || (!!stateObj && stateObj.state === "unknown") || (on && (kelvin == null || level == null)),
    };
  },

  /** What the card shows for its lamps. */
  cardView(views, { name, lang } = {}) {
    const t = strings(lang);
    const result = {
      title: name || (views.length === 1 ? views[0].name : t.lamps(views.length)),
      line: "",
      sceneLine: "",
      bulb: null,
      steps: { count: 0, filled: 0 },
      highlight: null,
      uncertain: false,
      uncertainHint: t.uncertain,
      disabled: true,
      loading: false,
      error: null,
    };
    const missing = views.find((v) => v.missing);
    const notLamp = views.find((v) => v.notLamp);
    const failed = views.find((v) => v.failedProfile);
    if (failed) return { ...result, error: t.profileFailed(failed.failedProfile) };
    if (missing) return { ...result, error: t.notFound(missing.id) };
    if (notLamp) return { ...result, error: t.notLamp(notLamp.id) };
    if (views.some((v) => v.loading)) return { ...result, loading: true, line: t.loading };
    if (new Set(views.map((v) => v.profile)).size > 1) return { ...result, error: t.mixed };

    const available = views.filter((v) => v.available);
    const lit = available.filter((v) => v.on);
    const first = lit[0] || null;
    result.disabled = available.length === 0;
    if (views.length === 1) {
      Object.assign(result, lampLine(views[0], t));
    } else {
      // "All …" only when it is true of every lamp of the card; otherwise counts.
      const gone = views.length - available.length;
      const dark = available.length - lit.length;
      if (lit.length === views.length) result.line = t.allOn;
      else if (dark === views.length) result.line = t.allOff;
      else if (gone === views.length) result.line = t.allWithoutPower;
      else if (!gone) result.line = t.someOn(lit.length, views.length);
      else {
        const parts = [];
        if (lit.length) parts.push(t.countOn(lit.length));
        if (dark) parts.push(t.countOff(dark));
        parts.push(t.withoutPower(gone));
        result.line = parts.join(" · ");
      }
    }
    if (first) {
      const scene = lit.every((v) => v.scene && v.scene === first.scene) ? first.scene : null;
      result.highlight = scene;
      result.sceneLine = scene ? first.labels[scene] || scene : "";
      if (first.kelvin != null) result.bulb = { kelvin: first.kelvin, level: first.level };
    }
    const levels = (first || views[0]).levels;
    const index = first && first.level != null ? levels.indexOf(first.level) : -1;
    result.steps = { count: levels.length, filled: index < 0 ? 0 : levels.length - index };
    return result;
  },

  /** green: sent to every lamp; yellow: to some; red: to none (or the call failed). */
  ledColor(entityIds, response) {
    if (!response) return "red";
    const sent = entityIds.filter((id) => response[id] && response[id].sent).length;
    if (sent === entityIds.length) return "green";
    return sent === 0 ? "red" : "yellow";
  },

  /** Which lamps did not get the command, and why; empty when all did. */
  ledMessage(entityIds, response, { names = {}, lang, error } = {}) {
    const t = strings(lang);
    if (!response) return `${t.notSent}: ${error || ""}`.trim();
    const parts = entityIds
      .filter((id) => !(response[id] && response[id].sent))
      .map((id) => {
        const answer = response[id];
        const reason = !answer ? t.reasons.absent : t.reasons[answer.reason] || answer.error || answer.reason;
        return `${names[id] || id} (${reason})`;
      });
    return parts.length ? `${t.notSent}: ${parts.join(", ")}` : "";
  },

  /**
   * The layout to draw. No automatic choice (owner, live check: it jumped as HA re-flowed the
   * sections); for a phone, a mini card with HA's Screen visibility condition. Vertical by default,
   * also for an old "adaptive" config; horizontal shrinks when it does not fit.
   */
  pickLayout(setting, cardWidth) {
    if (setting === "mini") return "mini";
    if (setting === "horizontal") {
      if (cardWidth >= HORIZONTAL_MIN) return "horizontal";
      return cardWidth >= MINI_MIN ? "mini" : "vertical";
    }
    return "vertical";
  },

  lampsTitle(n, lang) {
    return strings(lang).lamps(n);
  },

  /** A plain row of every command, for a profile without remote.json. */
  fallbackRemote(commands) {
    return {
      buttons: {},
      groups: { all: { kind: "row", wrap: true, buttons: [...commands] } },
      layouts: { vertical: [["all"]], horizontal: [["all"]], mini: [["all"]] },
    };
  },
};


/* ---------- On the dashboard ---------- */

const DOMAIN = "ble_mesh_lights";
const RETRY_SECONDS = [2, 5, 10, 30];
const LED_MS = 450;

const stroke = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">${body}</svg>`;
// Drawn after the Valen remote's own symbols (no manufacturer artwork).
const ICONS = {
  "valen:brighter": stroke('<circle cx="12" cy="12" r="3.2"/><path d="M12 3.5v2.3M12 18.2v2.3M3.5 12h2.3M18.2 12h2.3M6 6l1.6 1.6M16.4 16.4L18 18M6 18l1.6-1.6M16.4 7.6L18 6"/>'),
  "valen:dimmer": stroke('<circle cx="12" cy="12" r="2.6"/><path d="M12 6v1.6M12 16.4V18M6 12h1.6M16.4 12H18M7.8 7.8l1.1 1.1M15.1 15.1l1.1 1.1M7.8 16.2l1.1-1.1M15.1 8.9l1.1-1.1"/>'),
  "valen:relax": stroke('<circle cx="12" cy="12" r="7.5"/><path d="M12 8.2a3.8 3.8 0 1 1-3.6 2.6"/><path d="M8.4 7.6v3.3h3.2"/>'),
  "valen:concentrate": stroke('<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="4.4"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>'),
  "valen:nightlight": stroke('<path d="M15.5 4.8a7.6 7.6 0 1 0 3.7 12.4 6.2 6.2 0 0 1-3.7-12.4z"/>'),
  "valen:bright": stroke('<circle cx="12" cy="12" r="2.4" fill="currentColor"/><path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.8 2.8M15.2 15.2L18 18M6 18l2.8-2.8M15.2 8.8L18 6"/>'),
  "valen:energise": stroke('<circle cx="12" cy="12" r="7.5"/><path d="M4.6 13.3a7.5 7.5 0 0 0 14.8 0z" fill="currentColor"/>'),
};
// A stylized remote: the card's picture in the card picker and while no light is chosen.
const REMOTE_PICTURE = `<svg viewBox="0 0 60 110" width="60" height="110" aria-hidden="true">
  <rect x="2" y="2" width="56" height="106" rx="12" fill="var(--secondary-background-color, #f3f4f6)" stroke="var(--divider-color, #d2d6dc)" stroke-width="2"/>
  <circle cx="10" cy="10" r="2" fill="var(--primary-color, #03a9f4)"/>
  <rect x="10" y="14" width="16" height="9" rx="4.5" fill="var(--card-background-color, #fff)" stroke="var(--divider-color, #d2d6dc)"/>
  <rect x="34" y="14" width="16" height="9" rx="4.5" fill="var(--card-background-color, #fff)" stroke="var(--divider-color, #d2d6dc)"/>
  <circle cx="30" cy="45" r="16" fill="var(--card-background-color, #fff)" stroke="var(--divider-color, #d2d6dc)"/>
  <circle cx="30" cy="45" r="7" fill="var(--secondary-background-color, #f3f4f6)" stroke="var(--divider-color, #d2d6dc)"/>
  <g fill="var(--card-background-color, #fff)" stroke="var(--divider-color, #d2d6dc)">
    <circle cx="17" cy="72" r="5"/><circle cx="43" cy="72" r="5"/><circle cx="30" cy="82" r="5"/>
    <circle cx="17" cy="92" r="5"/><circle cx="43" cy="92" r="5"/>
  </g>
</svg>`;

const BULB =
  '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.7"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/></svg>';
const KELVIN_COLORS = [
  [2200, "#ff9a3c"],
  [2700, "#ffb45e"],
  [3000, "#ffc77f"],
  [4000, "#ffe6c2"],
  [6500, "#dfeeff"],
];
const kelvinColor = (kelvin) =>
  KELVIN_COLORS.reduce((best, item) => (Math.abs(item[0] - kelvin) < Math.abs(best[0] - kelvin) ? item : best))[1];

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLE = `
  :host { display: block; }
  ha-card { position: relative; padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
  .led { position: absolute; top: 9px; left: 9px; width: 6px; height: 6px; border-radius: 50%;
    background: var(--disabled-color, #bdbdbd); }
  .led.green { animation: blink-green ${LED_MS}ms ease-out; }
  .led.yellow { animation: blink-yellow ${LED_MS * 2}ms ease-out; }
  .led.red { animation: blink-red ${LED_MS * 2}ms ease-out; }
  @keyframes blink-green { 0%, 60% { background: var(--success-color, #43a047); box-shadow: 0 0 8px var(--success-color, #43a047); } }
  @keyframes blink-yellow { 0%, 30%, 60%, 80% { background: var(--warning-color, #ffa600); box-shadow: 0 0 8px var(--warning-color, #ffa600); } 40%, 50% { background: var(--disabled-color, #bdbdbd); } }
  @keyframes blink-red { 0%, 30%, 60%, 80% { background: var(--error-color, #db4437); box-shadow: 0 0 8px var(--error-color, #db4437); } 40%, 50% { background: var(--disabled-color, #bdbdbd); } }
  .placeholder { display: flex; align-items: center; gap: 16px; color: var(--secondary-text-color); font-size: 13px; }
  .placeholder svg { flex: none; }
  .head { display: flex; align-items: center; gap: 10px; min-width: 0; cursor: pointer; border-radius: 8px; }
  .head:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .lamps { display: flex; flex-direction: column; gap: 2px; }
  .lamp { display: flex; justify-content: space-between; gap: 12px; min-width: 0; padding: 6px 8px; border: 0;
    border-radius: 8px; background: transparent; color: var(--primary-text-color); font: inherit; font-size: 13px;
    cursor: pointer; text-align: left; }
  .lamp:hover, .lamp:focus-visible { background: rgba(127, 127, 127, 0.12); outline: none; }
  .lamp .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lamp .state { white-space: nowrap; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .bulb { width: 34px; height: 34px; flex: none; border-radius: 50%; display: grid; place-items: center;
    background: var(--disabled-color, #bdbdbd); }
  .bulb svg { width: 20px; height: 20px; }
  .texts { min-width: 0; }
  .title, .line, .scene { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .line, .scene { line-height: 18px; height: 18px; }
  .unsure { margin-left: 3px; cursor: help; }
  .title { font-size: 15px; font-weight: 500; color: var(--primary-text-color); }
  .line { font-size: 12.5px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  .scene { font-size: 12.5px; color: var(--primary-text-color); }
  .steps { display: flex; gap: 4px; }
  .steps i { flex: 1; height: 5px; border-radius: 3px; background: var(--divider-color, #e0e0e0); }
  .steps i.on { background: var(--primary-color); }
  .message { color: var(--secondary-text-color); font-size: 13px; }
  .body { display: flex; gap: 16px; align-items: center; justify-content: center; }
  .column { display: flex; flex-direction: column; gap: 12px; align-items: stretch; min-width: 0; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; }
  .mini .row { flex-wrap: nowrap; }
  .mini .row.wrap { flex-wrap: wrap; }
  .mini .row .k { flex: 1; min-width: 0; }
  .grid { display: grid; gap: 8px; justify-items: center; }
  button.k { font: 500 12px/1 var(--paper-font-body1_-_font-family, inherit); letter-spacing: .06em;
    color: var(--primary-text-color); background: var(--secondary-background-color, #f3f4f6);
    border: 1px solid var(--divider-color, #d2d6dc); border-radius: 999px; min-width: 44px; height: 40px;
    padding: 0 12px; display: grid; place-items: center; cursor: pointer; position: relative;
    box-shadow: 0 1px 0 var(--divider-color, #d2d6dc); white-space: nowrap; }
  .grid button.k, button.k.icon { width: 44px; height: 44px; padding: 0; }
  button.k:hover:not(:disabled) { filter: brightness(0.95); }
  button.k:active:not(:disabled) { transform: translateY(1px); box-shadow: none; }
  button.k:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  button.k:disabled { opacity: .45; cursor: default; }
  button.k.sel { border-color: var(--primary-color); box-shadow: 0 0 0 1px var(--primary-color); }
  button.k svg, button.k ha-icon { width: 20px; height: 20px; --mdc-icon-size: 20px; }
  .ring { position: relative; width: 132px; height: 132px; border-radius: 50%; margin: 0 auto;
    background: var(--secondary-background-color, #f3f4f6); border: 1px solid var(--divider-color, #d2d6dc); }
  .ring .half { position: absolute; left: 0; right: 0; height: 50%; border: 0; background: transparent;
    color: var(--primary-text-color); cursor: pointer; display: flex; justify-content: center; padding: 10px 0; }
  .ring .half svg, .ring .half ha-icon { width: 18px; height: 18px; --mdc-icon-size: 18px; }
  .ring .up { top: 0; align-items: flex-start; border-radius: 999px 999px 0 0; }
  .ring .down { bottom: 0; align-items: flex-end; border-radius: 0 0 999px 999px; }
  .ring .half:hover:not(:disabled) { background: rgba(127, 127, 127, 0.12); }
  .ring .half:active:not(:disabled) { transform: translateY(1px); }
  .ring .half:disabled { opacity: .45; cursor: default; }
  .ring .center { position: absolute; inset: 0; margin: auto; width: 64px; height: 64px; padding: 0; z-index: 1; }
  @media (prefers-reduced-motion: reduce) { .led { animation-duration: 1ms !important; } button.k:active { transform: none; } }
`;

const CardBase = typeof HTMLElement !== "undefined" ? HTMLElement : class {};

class BleMeshRemoteCard extends CardBase {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = { entities: [] };
    this._hass = null;
    this._info = null; // answer of ble_mesh_lights/lamps
    this._infoKey = "";
    this._fetching = false;
    this._retry = 0;
    this._retryTimer = null;
    this._connected = false;
    this._expanded = false; // the list of lamps, opened from the header of a several-lamp card
    this._width = 0;
    this._signature = "";
    this._resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => this._resized()) : null;
  }

  static getConfigElement() {
    return document.createElement("ble-mesh-remote-card-editor");
  }

  static getStubConfig(hass) {
    const lamp = Object.keys((hass && hass.states) || {}).find(
      (id) => id.startsWith("light.") && hass.entities && hass.entities[id] && hass.entities[id].platform === DOMAIN,
    );
    return { entities: lamp ? [lamp] : [], layout: "vertical" };
  }

  setConfig(config) {
    if (!config || (config.entities !== undefined && !Array.isArray(config.entities))) {
      throw new Error("entities must be a list of lights");
    }
    this._config = { layout: "vertical", hide_header: false, ...config, entities: config.entities || [] };
    this._signature = "";
    if (this._hass) this.hass = this._hass;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._config.entities.length === 0) {
      // No light chosen yet (the card picker's preview, a card just added): a picture, no error.
      if (this._signature !== "placeholder") {
        this._signature = "placeholder";
        this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="placeholder">${REMOTE_PICTURE}<span>${escapeHtml(
          strings(hass.language).chooseLamps,
        )}</span></div></ha-card>`;
      }
      return;
    }
    const key = `${this._config.entities.join(",")}|${hass.language}`;
    if (key !== this._infoKey) {
      this._infoKey = key;
      this._info = null;
      this._retry = 0;
      this._fetch();
    }
    this._render();
  }

  connectedCallback() {
    this._connected = true;
    if (this._resize) this._resize.observe(this);
    // Detached meanwhile (e.g. another view shown while HA started), the retry was stopped:
    // start again, or the card would stay on "Loading…" until a page reload.
    if (this._hass && !(this._info && this._info.loaded)) this._fetch();
  }

  disconnectedCallback() {
    this._connected = false;
    if (this._resize) this._resize.disconnect();
    clearTimeout(this._retryTimer);
    this._retryTimer = null;
  }

  getCardSize() {
    return { vertical: 7, horizontal: 4, mini: 3 }[this._configuredLayout()];
  }

  getGridOptions() {
    // From the configured layout, never the drawn one: the card's size must not follow its width.
    // Out of a section's 12 columns: half fits the vertical (~200 px) and mini (~240 px) layouts,
    // the horizontal one (~400 px) needs about the whole section (live check: 4 cut the vertical).
    return {
      vertical: { columns: 6, min_columns: 6 },
      horizontal: { columns: 12, min_columns: 9 },
      mini: { columns: 6, min_columns: 6 },
    }[this._configuredLayout()];
  }

  _configuredLayout() {
    const layout = this._config.layout;
    return layout === "horizontal" || layout === "mini" ? layout : "vertical";
  }

  async _fetch() {
    if (!this._hass || this._fetching || this._config.entities.length === 0) return;
    this._fetching = true;
    clearTimeout(this._retryTimer);
    const key = this._infoKey;
    let info = null;
    try {
      info = await this._hass.callWS({
        type: `${DOMAIN}/lamps`,
        entity_ids: this._config.entities,
        language: this._hass.language,
      });
    } catch (err) {
      info = null; // e.g. the integration is still starting: the command does not exist yet
    }
    this._fetching = false;
    if (key !== this._infoKey) {
      this._fetch(); // the lamps or the language changed meanwhile: this answer is for the old ones
      return;
    }
    this._info = info;
    if (!this._connected) {
      this._retryTimer = null; // a detached card does not poll; connectedCallback starts again
    } else if (!this._info || !this._info.loaded) {
      const seconds = RETRY_SECONDS[Math.min(this._retry, RETRY_SECONDS.length - 1)];
      this._retry += 1;
      this._retryTimer = setTimeout(() => this._fetch(), seconds * 1000);
    } else {
      this._retry = 0;
    }
    this._render();
  }

  _resized() {
    const width = this.clientWidth;
    if (width === this._width) return;
    this._width = width;
    this._render();
  }

  _views() {
    const info = this._info || { loaded: false, lamps: {}, failed: {} };
    return this._config.entities.map((id) =>
      RemoteModel.lampView(id, this._hass.states[id], info.lamps[id], !!info.loaded, (info.failed || {})[id]),
    );
  }

  _remote(views) {
    const lamp = views.find((v) => this._info && this._info.lamps[v.id]);
    const info = lamp && this._info.lamps[lamp.id];
    if (!info) return null;
    return info.remote || RemoteModel.fallbackRemote(info.commands);
  }

  _render() {
    if (!this._hass || this._config.entities.length === 0) return; // the picture stays
    const views = this._views();
    const card = RemoteModel.cardView(views, { name: this._config.name, lang: this._hass.language });
    const layout = RemoteModel.pickLayout(this._config.layout, this._width || this.clientWidth || 300);
    const remote = this._remote(views);
    this._labels = (views[0] && views[0].labels) || {};
    this._names = Object.fromEntries(views.map((v) => [v.id, v.name]));
    const lines = this._expanded && views.length > 1 ? RemoteModel.lampLines(views, this._hass.language) : null;
    // The DOM is built only when its structure changes; state changes are patched in, so a tap
    // that lands while a press updates the lamp is not lost and the LED blink is not cut short.
    const structure = JSON.stringify([
      layout, remote, this._config.hide_header, card.error, card.loading, card.steps.count, lines && lines.map((l) => l.id),
    ]);
    if (structure !== this._signature) {
      this._signature = structure;
      this._build(card, layout, remote, lines);
    }
    this._patch(card, lines);
  }

  _headerTapped() {
    const entities = this._config.entities;
    if (entities.length === 1) {
      this._lampTapped(entities[0]);
      return;
    }
    this._expanded = !this._expanded;
    this._render();
  }

  _lampTapped(entityId) {
    // HA opens the entity's own dialog (more-info) for this event.
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _build(card, layout, remote, lines) {
    const list = lines
      ? `<div class="lamps">${lines
          .map(
            (l) => `<button class="lamp" data-entity="${escapeHtml(l.id)}"><span class="name"></span><span class="state"></span></button>`,
          )
          .join("")}</div>`
      : "";
    const header = this._config.hide_header
      ? ""
      : `<div class="head" role="button" tabindex="0">
          <div class="bulb">${BULB}</div>
          <div class="texts"><div class="title"></div><div class="line"><span class="value"></span><span class="unsure" hidden>?</span></div><div class="scene"></div></div>
        </div>
        <div class="steps">${"<i></i>".repeat(card.steps.count)}</div>${list}`;
    let body = "";
    if (card.error) body = `<div class="message">${escapeHtml(card.error)}</div>`;
    else if (remote) body = this._body(remote, layout);
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card><span class="led"></span>${header}${body}</ha-card>`;
    this.shadowRoot.querySelectorAll("[data-command]").forEach((button) =>
      button.addEventListener("click", () => this._press(button.dataset.command)),
    );
    const head = this.shadowRoot.querySelector(".head");
    if (head) {
      head.addEventListener("click", () => this._headerTapped());
      head.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this._headerTapped();
        }
      });
    }
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((button) =>
      button.addEventListener("click", () => this._lampTapped(button.dataset.entity)),
    );
  }

  _patch(card, lines) {
    const root = this.shadowRoot;
    const text = (selector, value) => {
      const element = root.querySelector(selector);
      if (element && element.textContent !== value) element.textContent = value;
    };
    text(".title", card.title);
    text(".line .value", card.line);
    const unsure = root.querySelector(".unsure");
    if (unsure) {
      unsure.hidden = !card.uncertain;
      unsure.title = card.uncertainHint;
    }
    text(".scene", card.sceneLine);
    const bulb = root.querySelector(".bulb");
    if (bulb) {
      const color = card.bulb ? kelvinColor(card.bulb.kelvin) : "";
      bulb.style.background = color;
      bulb.style.boxShadow = card.bulb ? `0 0 ${6 + (card.bulb.level || 0) / 6}px ${color}` : "";
    }
    root.querySelectorAll(".steps i").forEach((step, i) => step.classList.toggle("on", i < card.steps.filled));
    for (const l of lines || []) {
      text(`[data-entity="${l.id}"] .name`, l.name);
      text(`[data-entity="${l.id}"] .state`, l.uncertain ? `${l.line}?` : l.line);
    }
    const disabled = card.disabled || card.loading || !!card.error;
    root.querySelectorAll("[data-command]").forEach((button) => {
      button.disabled = disabled;
      button.classList.toggle("sel", button.dataset.command === card.highlight);
    });
  }

  _body(remote, layout) {
    const buttons = remote.buttons || {};
    const mini = layout === "mini";
    const face = (command) => {
      const spec = buttons[command] || {};
      if (spec.icon && spec.icon.startsWith("mdi:")) return { html: `<ha-icon icon="${escapeHtml(spec.icon)}"></ha-icon>`, icon: true };
      if (spec.icon && ICONS[spec.icon]) return { html: ICONS[spec.icon], icon: true };
      const text = (mini && spec.short) || spec.label || this._labels[command] || command;
      return { html: escapeHtml(text), icon: false };
    };
    const key = (command, extra = "") => {
      const { html, icon } = face(command);
      const label = escapeHtml(this._labels[command] || command);
      return `<button class="k ${icon ? "icon" : ""} ${extra}" data-command="${escapeHtml(command)}"
        title="${label}" aria-label="${label}">${html}</button>`;
    };
    const group = (name) => {
      const spec = remote.groups[name];
      if (spec.kind === "row") {
        return `<div class="row ${spec.wrap ? "wrap" : ""}">${spec.buttons.map((c) => key(c)).join("")}</div>`;
      }
      if (spec.kind === "ring") {
        const half = (command, cls) => {
          const label = escapeHtml(this._labels[command] || command);
          return `<button class="half ${cls}" data-command="${escapeHtml(command)}" title="${label}" aria-label="${label}">${face(command).html}</button>`;
        };
        return `<div class="ring">${half(spec.up, "up")}${half(spec.down, "down")}${key(spec.center, "center")}</div>`;
      }
      const cells = spec.cells
        .map(([command, column, row]) => `<div style="grid-column:${column};grid-row:${row}">${key(command)}</div>`)
        .join("");
      return `<div class="grid" style="grid-template-columns:repeat(${spec.columns}, 1fr)">${cells}</div>`;
    };
    const columns = remote.layouts[layout] || remote.layouts.vertical;
    return `<div class="body ${layout}">${columns
      .map((column) => `<div class="column">${column.map(group).join("")}</div>`)
      .join("")}</div>`;
  }

  async _press(command) {
    const entities = this._config.entities;
    // Only lamps HA has available: HA would skip the others with a warning on every press,
    // and refuse a call that asks for a response and matches nothing (helpers/service.py).
    const targets = entities.filter((id) => this._hass.states[id] && this._hass.states[id].state !== "unavailable");
    // No lamp to send to: every lamp is "no power" (an empty answer), not a failed call.
    let response = targets.length ? null : {};
    let error = null;
    if (targets.length) {
      try {
        const answer = await this._hass.callWS({
          type: "call_service",
          domain: DOMAIN,
          service: "send_command",
          service_data: { command, queue: true },
          target: { entity_id: targets },
          return_response: true,
        });
        response = answer.response || {};
      } catch (err) {
        error = (err && err.message) || String(err);
      }
    }
    const color = RemoteModel.ledColor(entities, response);
    this._blink(color);
    if (color !== "green") {
      const message = RemoteModel.ledMessage(entities, response, { names: this._names, lang: this._hass.language, error });
      this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
    }
  }

  _blink(color) {
    const led = this.shadowRoot.querySelector(".led");
    if (!led) return;
    led.className = "led";
    void led.offsetWidth; // restart the animation
    led.className = `led ${color}`;
  }
}

class BleMeshRemoteCardEditor extends CardBase {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._lamps = null; // every lamp: entity id → info
    this._form = null;
  }

  setConfig(config) {
    this._config = { layout: "vertical", hide_header: false, ...config };
    if (!LAYOUT_OPTIONS.includes(this._config.layout)) this._config.layout = "vertical"; // e.g. an old "adaptive"
    this._update();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._loadLamps();
    this._update();
  }

  async _loadLamps() {
    try {
      const answer = await this._hass.callWS({ type: `${DOMAIN}/lamps` });
      this._lamps = answer.loaded ? answer.lamps : null;
    } catch (err) {
      this._lamps = null;
    }
    this._update();
  }

  _schema() {
    const t = strings(this._hass && this._hass.language).editor;
    const entity = { multiple: true, filter: { integration: DOMAIN, domain: "light" } };
    const first = (this._config.entities || [])[0];
    // After the first lamp only lamps of its profile (one card, one profile).
    if (this._lamps && first && this._lamps[first]) {
      const profile = this._lamps[first].profile;
      entity.include_entities = Object.keys(this._lamps).filter((id) => this._lamps[id].profile === profile);
    }
    return [
      { name: "entities", required: true, selector: { entity } },
      { name: "name", selector: { text: {} } },
      {
        name: "layout",
        selector: {
          select: { mode: "dropdown", options: LAYOUT_OPTIONS.map((value) => ({ value, label: t.layouts[value] })) },
        },
      },
      { name: "hide_header", selector: { boolean: {} } },
    ];
  }

  _update() {
    if (!this._hass) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (field) => strings(this._hass.language).editor[field.name] || field.name;
      this._form.addEventListener("value-changed", (event) => {
        this._config = { ...this._config, ...event.detail.value };
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
        this._update();
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = this._schema();
  }
}

const registerElements = () => {
  // Read window.customElements at call time: the frontend replaces it (see below).
  const registry = window.customElements;
  const define = (name, element) => {
    if (!registry.get(name)) registry.define(name, element);
  };
  define("ble-mesh-remote-card", BleMeshRemoteCard);
  define("ble-mesh-remote-card-editor", BleMeshRemoteCardEditor);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === "ble-mesh-remote-card")) {
    window.customCards.push({
      type: "ble-mesh-remote-card",
      name: "BLE Mesh remote",
      description: "Virtual remote for BLE Mesh lights",
      preview: true, // the picker draws the card (getStubConfig), or its picture without lights

    });
  }
};

/*
 * The Home Assistant frontend replaces window.customElements with a scoped registry when it
 * boots; a module that runs earlier (after a hard reload) would define its elements in the old
 * registry, invisible to the frontend. So register once the frontend's root element exists
 * (as the Ferroli schedule card does).
 */
const registerWhenFrontendReady = (attempt = 0) => {
  if (window.customElements.get("home-assistant") || attempt > 600) {
    registerElements();
  } else {
    setTimeout(() => registerWhenFrontendReady(attempt + 1), 50);
  }
};

if (typeof window !== "undefined" && window.customElements) registerWhenFrontendReady();

if (typeof module !== "undefined") module.exports = { RemoteModel, VERTICAL_MIN, MINI_MIN, HORIZONTAL_MIN };
