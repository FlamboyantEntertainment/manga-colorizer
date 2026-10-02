import { DEFAULT_SETTINGS, PRESETS, SERVER_URL, SIZES } from "./config.js";
import { matchingPreset, normalizeSettings, storedSettings } from "./lib.js";

const HEALTH_TIMEOUT_MS = 2000;
const SETTING_NAMES = Object.keys(DEFAULT_SETTINGS);
const FORMATS = {
  saturation: (value) => `${Math.round(value * 100)}%`,
  warmth: (value) => `${value > 0 ? "+" : ""}${Math.round(value * 100)}`,
  hue: (value) => `${value > 0 ? "+" : ""}${value}°`,
  clean_whites: (value) => `${Math.round(value * 100)}%`,
  denoise: (value) => String(value),
};
const $ = (id) => document.getElementById(id);

init();

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const stored = await chrome.storage.local.get(["enabledOrigins", "settings", "preset", "showOriginals"]);
  setUpSiteToggle(tab, stored.enabledOrigins ?? []);
  setUpSettings(storedSettings(stored));
  $("originals").checked = stored.showOriginals ?? false;
  $("originals").addEventListener("change", (event) => {
    chrome.storage.local.set({ showOriginals: event.target.checked });
  });
  showServerStatus();
}

function setUpSiteToggle(tab, enabledOrigins) {
  const url = tab && tab.url ? new URL(tab.url) : null;
  if (!url || !/^https?:$/.test(url.protocol)) {
    $("site").disabled = true;
    $("origin").textContent = "this page";
    showNote("Only works on regular web pages.");
    return;
  }
  $("origin").textContent = url.host;
  $("site").checked = enabledOrigins.includes(url.origin);
  $("site").addEventListener("change", async (event) => {
    const enabled = event.target.checked;
    const response = await chrome.runtime.sendMessage({ type: "set-site", origin: url.origin, enabled, tabId: tab.id });
    if (!response || !response.ok) {
      event.target.checked = !enabled;
      showNote(`Couldn't change this site: ${response ? response.error : "no response"}`);
    }
  });
}

function setUpSettings(initial) {
  let saved = initial;
  for (const size of SIZES) {
    $("size").append(new Option(size.label, String(size.value)));
  }
  for (const [name, preset] of Object.entries(PRESETS)) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.dataset.preset = name;
    chip.textContent = preset.label;
    chip.addEventListener("click", () => commit({ ...readSettings(), ...preset.tone }));
    $("presets").append(chip);
  }
  for (const name of SETTING_NAMES) {
    const input = $(name);
    // Show values while dragging; only recolor once the slider is let go.
    input.addEventListener("input", () => showSettings(readSettings()));
    input.addEventListener("change", () => commit(readSettings()));
    if (input.type === "range") {
      input.addEventListener("dblclick", () => commit({ ...readSettings(), [name]: DEFAULT_SETTINGS[name] }));
    }
  }
  $("reset").addEventListener("click", () => commit(DEFAULT_SETTINGS));
  showSettings(saved);

  function commit(next) {
    const settings = normalizeSettings(next);
    showSettings(settings);
    if (SETTING_NAMES.some((name) => settings[name] !== saved[name])) {
      saved = settings;
      chrome.storage.local.set({ settings });
    }
  }
}

function readSettings() {
  const out = {};
  for (const name of SETTING_NAMES) {
    const input = $(name);
    out[name] = input.type === "checkbox" ? input.checked : Number(input.value);
  }
  return normalizeSettings(out);
}

function showSettings(settings) {
  for (const name of SETTING_NAMES) {
    const input = $(name);
    if (input.type === "checkbox") {
      input.checked = settings[name];
    } else {
      input.value = String(settings[name]);
    }
  }
  for (const output of document.querySelectorAll("output[data-for]")) {
    output.textContent = FORMATS[output.dataset.for](settings[output.dataset.for]);
  }
  const active = matchingPreset(settings);
  for (const chip of $("presets").children) {
    chip.setAttribute("aria-pressed", String(chip.dataset.preset === active));
  }
}

async function showServerStatus() {
  try {
    const response = await fetch(`${SERVER_URL}/api/health`, {
      cache: "no-store", signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    });
    const health = await response.json();
    if (health.state === "ready") {
      setStatus("ok", `Server ready · ${health.device}`);
    } else if (health.state === "loading") {
      setStatus("wait", "Server is loading the model…");
    } else {
      setStatus("error", `Model failed to load: ${health.error}`);
    }
  } catch {
    setStatus("error", "Server offline. Start the Manga Colorizer server (Docker or ./run.sh).");
  }
}

function setStatus(kind, text) {
  $("server").className = `status ${kind}`;
  $("server").textContent = text;
}

function showNote(text) {
  $("note").textContent = text;
  $("note").hidden = false;
}
