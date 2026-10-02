// Service worker: per-site toggle, content script registration, and the colorize queue.
// Page images are downloaded here (host permissions avoid CORS) and sent to the local server.
import { SERVER_URL } from "./config.js";
import {
  JobQueue, LruCache, base64ToBytes, bytesToBase64, normalizeSettings, refererRuleId, settingsKey, storedSettings,
} from "./lib.js";

const SCRIPT_ID = "manga-colorizer";
const PORT_NAME = "manga-colorizer";
const CACHE_CAPACITY = 40;
const OFFLINE_PAUSE_MS = 5000;

class ServerOffline extends Error {}

const queue = new JobQueue();
const cache = new LruCache(CACHE_CAPACITY);
const ports = new Map(); // port id -> Port
const refererByHost = new Map();
let nextPortId = 1;
let running = null; // the job currently on the server
let busy = false;
let pausedUntil = 0;

chrome.runtime.onInstalled.addListener(restoreContentScripts);
chrome.runtime.onStartup.addListener(restoreContentScripts);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "set-site") {
    return false;
  }
  setSite(message.origin, message.enabled, message.tabId).then(
    () => sendResponse({ ok: true }),
    (error) => sendResponse({ ok: false, error: String(error) }),
  );
  return true;
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) {
    return;
  }
  const portId = nextPortId++;
  ports.set(portId, port);
  port.onMessage.addListener((message) => onPortMessage(portId, message));
  port.onDisconnect.addListener(() => {
    ports.delete(portId);
    const prefix = `${portId}:`;
    queue.removeWaitersWhere((waiterKey) => waiterKey.startsWith(prefix));
    if (running) {
      for (const waiterKey of [...running.waiters.keys()]) {
        if (waiterKey.startsWith(prefix)) {
          running.waiters.delete(waiterKey);
        }
      }
    }
  });
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.settings) {
    const current = settingsKey(changes.settings.newValue);
    queue.removeJobsWhere((job) => job.payload.settingsKey !== current);
  }
});

async function restoreContentScripts() {
  const stored = await chrome.storage.local.get(["enabledOrigins", "settings", "preset"]);
  if (!stored.settings) {
    await chrome.storage.local.set({ settings: storedSettings(stored) });
    await chrome.storage.local.remove("preset");
  }
  await syncContentScripts(stored.enabledOrigins ?? []);
}

async function setSite(origin, enabled, tabId) {
  const { enabledOrigins = [] } = await chrome.storage.local.get("enabledOrigins");
  const next = enabled
    ? [...new Set([...enabledOrigins, origin])]
    : enabledOrigins.filter((candidate) => candidate !== origin);
  await chrome.storage.local.set({ enabledOrigins: next });
  await syncContentScripts(next);
  if (enabled && tabId != null) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  }
}

async function syncContentScripts(origins) {
  await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] }).catch(() => {});
  if (origins.length === 0) {
    return;
  }
  await chrome.scripting.registerContentScripts([{
    id: SCRIPT_ID,
    js: ["content.js"],
    matches: origins.map((origin) => `${origin}/*`),
    runAt: "document_idle",
    persistAcrossSessions: true,
  }]);
}

function onPortMessage(portId, message) {
  if (message.type === "colorize") {
    const settings = normalizeSettings(message.settings);
    const key = `${settingsKey(settings)} ${message.url}`;
    const waiterKey = `${portId}:${message.id}`;
    const cached = cache.get(key);
    if (cached) {
      deliver(waiterKey, cached);
      return;
    }
    if (running && running.key === key) {
      running.waiters.set(waiterKey, message.distance);
      return;
    }
    const payload = {
      url: message.url, pageUrl: message.pageUrl, settings, settingsKey: settingsKey(settings),
      bytes: message.bytes ?? null,
    };
    queue.add(key, payload, waiterKey, message.distance);
    pump();
  } else if (message.type === "priority") {
    for (const [id, distance] of message.items) {
      queue.setDistance(`${portId}:${id}`, distance);
    }
  } else if (message.type === "cancel") {
    const waiterKey = `${portId}:${message.id}`;
    queue.removeWaitersWhere((candidate) => candidate === waiterKey);
  }
}

async function pump() {
  if (busy) {
    return;
  }
  busy = true;
  try {
    while (queue.size > 0) {
      const wait = pausedUntil - Date.now();
      if (wait > 0) {
        setTimeout(pump, wait);
        return;
      }
      running = queue.pop();
      const result = await processJob(running.payload);
      const job = running;
      running = null;
      for (const waiterKey of job.waiters.keys()) {
        deliver(waiterKey, result);
      }
      if (result.status === "offline") {
        pausedUntil = Date.now() + OFFLINE_PAUSE_MS;
        for (const waiterKey of queue.waiterKeys()) {
          deliver(waiterKey, result);
        }
        queue.requeue(job);
      } else if (result.status !== "failed") {
        cache.set(job.key, result);
      }
    }
  } finally {
    busy = false;
  }
}

async function processJob({ url, pageUrl, settings, bytes }) {
  try {
    const image = bytes ? new Blob([base64ToBytes(bytes)]) : await fetchImage(url, pageUrl);
    return await colorizeOnServer(image, settings);
  } catch (error) {
    return { status: error instanceof ServerOffline ? "offline" : "failed" };
  }
}

async function fetchImage(url, pageUrl) {
  await setReferer(url, pageUrl).catch(() => {});
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`Image request failed with ${response.status}`);
  }
  return response.blob();
}

// Many image CDNs refuse requests that don't come from the reading site.
async function setReferer(imageUrl, pageUrl) {
  const host = new URL(imageUrl).hostname;
  if (refererByHost.get(host) === pageUrl) {
    return;
  }
  const id = refererRuleId(host);
  await chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [id],
    addRules: [{
      id,
      priority: 1,
      action: {
        type: "modifyHeaders",
        requestHeaders: [{ header: "referer", operation: "set", value: pageUrl }],
      },
      condition: {
        requestDomains: [host],
        tabIds: [chrome.tabs.TAB_ID_NONE], // only requests made by this service worker
        resourceTypes: ["xmlhttprequest", "other"],
      },
    }],
  });
  refererByHost.set(host, pageUrl);
}

async function colorizeOnServer(image, settings) {
  const form = new FormData();
  form.append("file", image, "page");
  const params = new URLSearchParams(Object.entries(settings).map(([name, value]) => [name, String(value)]));
  let response;
  try {
    response = await fetch(`${SERVER_URL}/api/colorize?${params}`, { method: "POST", body: form });
  } catch {
    throw new ServerOffline();
  }
  if (response.status === 503) {
    throw new ServerOffline();
  }
  if (response.status === 204) {
    return { status: "skipped" };
  }
  if (!response.ok) {
    return { status: "failed" };
  }
  const colored = new Uint8Array(await response.arrayBuffer());
  return { status: "done", jpegBase64: bytesToBase64(colored) };
}

function deliver(waiterKey, result) {
  const separator = waiterKey.indexOf(":");
  const port = ports.get(Number(waiterKey.slice(0, separator)));
  if (!port) {
    return;
  }
  try {
    port.postMessage({ type: "result", id: Number(waiterKey.slice(separator + 1)), ...result });
  } catch {
    // The tab navigated away; onDisconnect cleans up.
  }
}
