// Injected only on sites turned on in the popup. Finds manga page images, asks the
// service worker to colorize them, and swaps the colored versions in place.
(() => {
  // After an extension reload the old copy can linger with a dead chrome.runtime. It keeps
  // this flag set, so check that its owner is still alive before standing down.
  const owner = window.__mangaColorizer;
  if (typeof owner === "function" && owner()) {
    return;
  }
  const TAKEOVER_EVENT = "manga-colorizer-takeover";
  const token = Math.random().toString(36).slice(2);
  document.dispatchEvent(new CustomEvent(TAKEOVER_EVENT, { detail: token }));
  document.addEventListener(TAKEOVER_EVENT, (event) => {
    if (event.detail !== token) {
      stop();
    }
  });
  window.__mangaColorizer = () => active && isExtensionAlive();

  const PORT_NAME = "manga-colorizer";
  const MIN_SIDE = 500;
  const LOOKAHEAD_PX = 1500;
  const TICK_MS = 500;
  const RECONNECT_DELAY_MS = 1000;
  const BADGE_STYLE = "position:absolute;z-index:2147483647;pointer-events:none;margin:6px;" +
    "padding:2px 8px;border-radius:4px;font:12px/1.4 system-ui,sans-serif;" +
    "color:#fff;background:rgba(0,0,0,.72);";

  const states = new Map(); // img -> state
  const byId = new Map(); // request id -> state
  const waitingForLoad = new WeakSet();
  const intersection = new IntersectionObserver(onIntersect, {
    rootMargin: `${LOOKAHEAD_PX}px 0px`,
    scrollMargin: `${LOOKAHEAD_PX}px 0px`, // readers that scroll a <div> instead of the window
  });
  const mutations = new MutationObserver(onMutations);
  let nextId = 1;
  let port = null;
  let settings = null; // the service worker fills in defaults
  let showOriginals = false;
  let active = true;
  let tickTimer = 0;

  start();

  async function start() {
    const stored = await chrome.storage.local.get(["enabledOrigins", "settings", "showOriginals"]);
    if (!(stored.enabledOrigins ?? []).includes(location.origin)) {
      active = false;
      window.__mangaColorizer = false;
      return;
    }
    settings = stored.settings ?? null;
    showOriginals = stored.showOriginals ?? false;
    chrome.storage.onChanged.addListener(onStorageChanged);
    mutations.observe(document.documentElement, {
      subtree: true, childList: true, attributes: true, attributeFilter: ["src", "srcset"],
    });
    for (const img of document.images) {
      consider(img);
    }
    tickTimer = setInterval(tick, TICK_MS);
  }

  function consider(img) {
    if (!active || states.has(img)) {
      return;
    }
    if (!img.complete || img.naturalWidth === 0) {
      if (!waitingForLoad.has(img)) {
        waitingForLoad.add(img);
        img.addEventListener("load", () => {
          waitingForLoad.delete(img);
          consider(img);
        }, { once: true });
      }
      return;
    }
    if (img.naturalWidth < MIN_SIDE || img.naturalHeight < MIN_SIDE) {
      return;
    }
    const state = {
      id: nextId++, img, status: "new", sourceUrl: img.currentSrc || img.src,
      coloredUrl: null, original: null, badge: null, message: null,
    };
    states.set(img, state);
    byId.set(state.id, state);
    intersection.observe(img);
  }

  function onIntersect(entries) {
    for (const entry of entries) {
      const state = states.get(entry.target);
      if (entry.isIntersecting && state && state.status === "new") {
        request(state);
      }
    }
  }

  async function request(state) {
    state.status = "pending";
    setBadge(state, "coloring…");
    const message = {
      type: "colorize", id: state.id, url: state.sourceUrl, pageUrl: location.href,
      settings, distance: distanceOf(state.img),
    };
    if (/^(blob|data):/.test(state.sourceUrl)) {
      try {
        message.bytes = await readImageBase64(state.img, state.sourceUrl);
      } catch {
        finish(state, "failed");
        return;
      }
    }
    if (byId.get(state.id) !== state) {
      return; // forgotten while reading
    }
    state.message = message;
    send(message);
  }

  function send(message) {
    if (!active) {
      return;
    }
    try {
      if (!port) {
        port = chrome.runtime.connect({ name: PORT_NAME });
        port.onMessage.addListener(onPortMessage);
        port.onDisconnect.addListener(onDisconnect);
      }
      port.postMessage(message);
    } catch {
      if (!isExtensionAlive()) {
        stop(); // the extension was reloaded or removed; this copy is orphaned
        return;
      }
      port = null;
      setTimeout(resendPending, RECONNECT_DELAY_MS);
    }
  }

  function isExtensionAlive() {
    try {
      return Boolean(chrome.runtime && chrome.runtime.id);
    } catch {
      return false;
    }
  }

  function resendPending() {
    for (const state of byId.values()) {
      if (state.status === "pending" && state.message) {
        send(state.message);
      }
    }
  }

  function post(message) {
    if (!port) {
      return;
    }
    try {
      port.postMessage(message);
    } catch {
      // Disconnected; onDisconnect resends what is still pending.
    }
  }

  function onDisconnect() {
    port = null;
    if (!active) {
      return;
    }
    setTimeout(resendPending, RECONNECT_DELAY_MS);
  }

  function onPortMessage(message) {
    if (message.type !== "result") {
      return;
    }
    const state = byId.get(message.id);
    if (!state || state.status !== "pending") {
      return;
    }
    if (message.status === "offline") {
      setBadge(state, "server offline");
      return;
    }
    if (message.status === "done") {
      state.coloredUrl = URL.createObjectURL(base64ToBlob(message.jpegBase64, "image/jpeg"));
      finish(state, "done");
      if (!showOriginals) {
        showColored(state);
      }
      return;
    }
    finish(state, message.status); // "skipped" | "failed": keep the original
  }

  function finish(state, status) {
    state.status = status;
    state.message = null;
    removeBadge(state);
  }

  function showColored(state) {
    const { img } = state;
    if (!state.original) {
      const parent = img.parentElement;
      const sources = parent && parent.tagName === "PICTURE" ? [...parent.querySelectorAll("source")] : [];
      state.original = {
        src: img.getAttribute("src"),
        srcset: img.getAttribute("srcset"),
        sources: sources.map((source) => [source, source.getAttribute("srcset")]),
      };
    }
    for (const [source] of state.original.sources) {
      source.removeAttribute("srcset");
    }
    img.removeAttribute("srcset");
    img.setAttribute("src", state.coloredUrl);
  }

  function showOriginal(state) {
    const { img, original } = state;
    if (!original) {
      return;
    }
    for (const [source, srcset] of original.sources) {
      if (srcset !== null) {
        source.setAttribute("srcset", srcset);
      }
    }
    if (original.srcset !== null) {
      img.setAttribute("srcset", original.srcset);
    }
    if (original.src !== null) {
      img.setAttribute("src", original.src);
    } else {
      img.removeAttribute("src");
    }
  }

  function onMutations(records) {
    for (const record of records) {
      if (record.type === "attributes") {
        if (record.target instanceof HTMLImageElement) {
          onImageChanged(record.target);
        }
        continue;
      }
      for (const node of record.addedNodes) {
        if (node instanceof HTMLImageElement) {
          consider(node);
        } else if (node instanceof Element) {
          for (const img of node.querySelectorAll("img")) {
            consider(img);
          }
        }
      }
    }
  }

  // The site changed src/srcset (lazy loaders do this): treat it as a new image.
  function onImageChanged(img) {
    const state = states.get(img);
    if (state && isOwnChange(state)) {
      return;
    }
    if (state) {
      forget(state);
    }
    consider(img);
  }

  function isOwnChange(state) {
    if (!state.original) {
      return false;
    }
    const src = state.img.getAttribute("src");
    const srcset = state.img.getAttribute("srcset");
    return (src === state.coloredUrl || src === state.original.src)
      && (srcset === null || srcset === state.original.srcset);
  }

  function forget(state) {
    if (state.status === "pending") {
      post({ type: "cancel", id: state.id });
    }
    removeBadge(state);
    if (state.coloredUrl) {
      URL.revokeObjectURL(state.coloredUrl);
    }
    intersection.unobserve(state.img);
    states.delete(state.img);
    byId.delete(state.id);
  }

  function onStorageChanged(changes, area) {
    if (area !== "local") {
      return;
    }
    if (changes.enabledOrigins && !(changes.enabledOrigins.newValue ?? []).includes(location.origin)) {
      stop();
      return;
    }
    if (changes.settings) {
      settings = changes.settings.newValue ?? null;
      for (const state of [...states.values()]) {
        showOriginal(state);
        forget(state);
        consider(state.img);
      }
    }
    if (changes.showOriginals) {
      showOriginals = changes.showOriginals.newValue ?? false;
      for (const state of states.values()) {
        if (state.status === "done") {
          if (showOriginals) {
            showOriginal(state);
          } else {
            showColored(state);
          }
        }
      }
    }
  }

  function stop() {
    if (!active) {
      return;
    }
    active = false;
    mutations.disconnect();
    for (const state of [...states.values()]) {
      showOriginal(state);
      forget(state);
    }
    intersection.disconnect();
    clearInterval(tickTimer);
    try {
      chrome.storage.onChanged.removeListener(onStorageChanged);
      if (port) {
        port.disconnect();
      }
    } catch {
      // Extension context already gone.
    }
    port = null;
    window.__mangaColorizer = false;
  }

  function tick() {
    const items = [];
    for (const state of [...states.values()]) {
      if (!state.img.isConnected) {
        forget(state);
        continue;
      }
      if (state.status !== "pending") {
        continue;
      }
      items.push([state.id, distanceOf(state.img)]);
      placeBadge(state);
    }
    if (items.length > 0) {
      post({ type: "priority", items });
    }
  }

  // 0 while on screen, otherwise pixels away from the viewport.
  function distanceOf(img) {
    const rect = img.getBoundingClientRect();
    if (rect.bottom < 0) {
      return 1 - rect.bottom;
    }
    if (rect.top > innerHeight) {
      return 1 + rect.top - innerHeight;
    }
    return 0;
  }

  function setBadge(state, text) {
    if (!state.badge) {
      state.badge = document.createElement("div");
      state.badge.setAttribute("style", BADGE_STYLE);
      document.body.append(state.badge);
    }
    state.badge.textContent = text;
    placeBadge(state);
  }

  function placeBadge(state) {
    if (!state.badge) {
      return;
    }
    const rect = state.img.getBoundingClientRect();
    state.badge.style.left = `${rect.left + scrollX}px`;
    state.badge.style.top = `${rect.top + scrollY}px`;
  }

  function removeBadge(state) {
    if (state.badge) {
      state.badge.remove();
      state.badge = null;
    }
  }

  // Readers that unscramble pages often revoke the blob URL once the image is shown; the
  // decoded image is still there, and a blob:/data: image never taints a canvas.
  async function readImageBase64(img, url) {
    try {
      return await blobToBase64(await (await fetch(url)).blob());
    } catch {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.getContext("2d").drawImage(img, 0, 0);
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) {
        throw new Error("Could not read the page image");
      }
      return blobToBase64(blob);
    }
  }

  async function blobToBase64(blob) {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    return dataUrl.slice(dataUrl.indexOf(",") + 1);
  }

  function base64ToBlob(base64, type) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) {
      bytes[index] = binary.charCodeAt(index);
    }
    return new Blob([bytes], { type });
  }
})();
