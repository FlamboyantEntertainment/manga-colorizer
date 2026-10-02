// Pure helpers for the service worker and popup. No chrome.* here, so they run under `node --test`.
import { DEFAULT_PRESET, DEFAULT_SETTINGS, PRESETS, SETTING_RANGES } from "./config.js";

const BASE64_CHUNK = 0x8000;
const MAX_RULE_ID = 2_000_000_000;

export class JobQueue {
  #jobs = new Map(); // job key -> { key, payload, waiters: Map<waiterKey, distance> }
  #jobKeyByWaiter = new Map();

  get size() {
    return this.#jobs.size;
  }

  add(key, payload, waiterKey, distance) {
    let job = this.#jobs.get(key);
    if (!job) {
      job = { key, payload, waiters: new Map() };
      this.#jobs.set(key, job);
    }
    job.waiters.set(waiterKey, distance);
    this.#jobKeyByWaiter.set(waiterKey, key);
  }

  setDistance(waiterKey, distance) {
    const job = this.#jobs.get(this.#jobKeyByWaiter.get(waiterKey));
    if (job && job.waiters.has(waiterKey)) {
      job.waiters.set(waiterKey, distance);
    }
  }

  removeWaitersWhere(predicate) {
    for (const [waiterKey, key] of this.#jobKeyByWaiter) {
      if (!predicate(waiterKey)) {
        continue;
      }
      this.#jobKeyByWaiter.delete(waiterKey);
      const job = this.#jobs.get(key);
      if (!job) {
        continue;
      }
      job.waiters.delete(waiterKey);
      if (job.waiters.size === 0) {
        this.#jobs.delete(key);
      }
    }
  }

  removeJobsWhere(predicate) {
    for (const job of this.#jobs.values()) {
      if (predicate(job)) {
        this.#forget(job);
      }
    }
  }

  pop() {
    let best = null;
    let bestDistance = Infinity;
    for (const job of this.#jobs.values()) {
      const distance = Math.min(...job.waiters.values());
      if (best === null || distance < bestDistance) {
        best = job;
        bestDistance = distance;
      }
    }
    if (best) {
      this.#forget(best);
    }
    return best;
  }

  requeue(job) {
    for (const [waiterKey, distance] of job.waiters) {
      this.add(job.key, job.payload, waiterKey, distance);
    }
  }

  waiterKeys() {
    return this.#jobKeyByWaiter.keys();
  }

  clear() {
    this.#jobs.clear();
    this.#jobKeyByWaiter.clear();
  }

  #forget(job) {
    this.#jobs.delete(job.key);
    for (const waiterKey of job.waiters.keys()) {
      if (this.#jobKeyByWaiter.get(waiterKey) === job.key) {
        this.#jobKeyByWaiter.delete(waiterKey);
      }
    }
  }
}

export class LruCache {
  #capacity;
  #entries = new Map();

  constructor(capacity) {
    this.#capacity = capacity;
  }

  get size() {
    return this.#entries.size;
  }

  get(key) {
    if (!this.#entries.has(key)) {
      return undefined;
    }
    const value = this.#entries.get(key);
    this.#entries.delete(key);
    this.#entries.set(key, value);
    return value;
  }

  set(key, value) {
    this.#entries.delete(key);
    this.#entries.set(key, value);
    while (this.#entries.size > this.#capacity) {
      this.#entries.delete(this.#entries.keys().next().value);
    }
  }

  clear() {
    this.#entries.clear();
  }
}

export function bytesToBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK));
  }
  return btoa(binary);
}

export function base64ToBytes(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

// FNV-1a, folded into the id range declarativeNetRequest accepts.
export function refererRuleId(host) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < host.length; index++) {
    hash ^= host.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return (hash % MAX_RULE_ID) + 1;
}

// Coerce whatever is in storage into the exact parameters /api/colorize accepts.
export function normalizeSettings(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const out = {};
  for (const [name, fallback] of Object.entries(DEFAULT_SETTINGS)) {
    if (typeof fallback === "boolean") {
      out[name] = typeof source[name] === "boolean" ? source[name] : fallback;
      continue;
    }
    const value = Number(source[name]);
    const [min, max] = SETTING_RANGES[name];
    out[name] = Number.isFinite(value) && source[name] !== "" ? Math.min(max, Math.max(min, value)) : fallback;
  }
  return out;
}

export function settingsKey(raw) {
  const settings = normalizeSettings(raw);
  return Object.keys(DEFAULT_SETTINGS).map((name) => `${name}=${settings[name]}`).join("&");
}

// Before sliders existed only a preset name was saved; carry it over.
export function storedSettings(stored) {
  if (stored.settings) {
    return normalizeSettings(stored.settings);
  }
  const preset = PRESETS[stored.preset] ?? PRESETS[DEFAULT_PRESET];
  return normalizeSettings({ ...DEFAULT_SETTINGS, ...preset.tone });
}

export function matchingPreset(settings) {
  for (const [name, preset] of Object.entries(PRESETS)) {
    if (Object.entries(preset.tone).every(([key, value]) => settings[key] === value)) {
      return name;
    }
  }
  return null;
}
