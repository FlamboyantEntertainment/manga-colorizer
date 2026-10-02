# Browser Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Brave (Chromium MV3) extension that colorizes manga page images in place, as you scroll, using the local Manga Colorizer server.

**Architecture:** The server gets one new endpoint, `POST /api/colorize`: one image in, the colored JPEG out. The extension has three parts:
- **Service worker (`background.js`):** owns per-site state, a priority queue and a small in-memory cache. It downloads page images with the extension's own permissions (adding a `Referer` rule per image host) and sends them to the server.
- **Content script (`content.js`):** injected only on sites turned on in the popup. It finds large `<img>` elements near the viewport, requests colorization over a long-lived port, and swaps blob URLs of the results in place.
- **Popup:** controls the per-site toggle, the preset and the "show originals" switch.

**Tech Stack:** Python 3.12, FastAPI, Pillow, pytest (+ httpx for `TestClient`); plain JavaScript (ES modules for the service worker and popup, a classic script for the content script); `node --test` (Node 24) for the pure helpers.

**Spec:** `docs/superpowers/specs/2026-10-02-browser-extension-design.md`

## Global Constraints

- Server URL: `http://127.0.0.1:7860`. Run with `./run.sh` from the repo root.
- Endpoint limits: uploads over 25 MB → `413`; more than 40 megapixels → `413`; short side under 256 px → `204`; already colored (with `skip_colored`) → `204`; model not ready → `503`; unreadable → `422`; success → `200 image/jpeg` quality 90, same pixel size as the input.
- Inference must go through `_GpuLockedColorizer` (it shares the one GPU with book jobs).
- The extension never writes colored pages to disk: tab blob URLs, plus a 40-entry in-memory LRU in the service worker.
- The candidate image size is `naturalWidth` ≥ 500 **and** `naturalHeight` ≥ 500. The lookahead is a 1500 px root margin.
- One request is in flight to the server at a time. Server offline or `503` pauses the queue for 5 s and labels the images "server offline".
- Presets must match `PRESETS` in `static/index.html` exactly: natural, vivid, soft, warm ("Warm vintage"), cool.
- **No git commands.** The user's rule is no `git add`/`commit`/`push`. The "Commit" step of each task is replaced by "Stop: no commit, the user commits."
- Python tests run with `uv run pytest`; JS tests run with `node --test tests/js/`.

## Review Focus

1. **Lazy-loaded images** (placeholder `src`, with the real URL swapped in later by the site): they should be colorized once the real image loads, not ignored because the placeholder was tiny. Pinned by the test page's `data-src` images (Task 6), handled in Task 4's `consider`/`onImageChanged`.
2. **Server stopped mid-chapter:** pages stay black and white with a "server offline" label, and color automatically once `./run.sh` is back, without reloading. Pinned by the manual offline step in Task 6, using the requeue logic in Task 3.
3. **Covers, color art and icons:** left untouched, with no label stuck on them. Pinned by the endpoint tests (`test_already_colored_returns_204`, `test_too_small_returns_204`) in Task 1 and the icon and color image on the test page (Task 6).
4. **Pages served as `blob:` URLs or inside `<picture>`:** still colorized, because the content script reads blob bytes itself and stashes the `<source>` srcsets. Pinned by the test page's blob and picture images (Task 6).
5. **Non-RGB uploads** (grayscale `L`, `LA`, palette `P`, `RGBA` PNGs that many scan sites serve): colorized, not a 500. Pinned by `test_accepts_common_image_modes` in Task 1.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `server.py` | modify | Add `POST /api/colorize`, `_open_image`, and the limit constants |
| `pyproject.toml` | modify | Add `httpx` to the dev group (needed by `fastapi.testclient`) |
| `tests/test_colorize_endpoint.py` | create | Endpoint tests with a fake colorizer |
| `extension/manifest.json` | create | MV3 manifest |
| `extension/config.js` | create | `SERVER_URL`, `DEFAULT_PRESET`, `PRESETS` |
| `extension/lib.js` | create | `JobQueue`, `LruCache`, `bytesToBase64`, `base64ToBytes`, `refererRuleId` (no `chrome.*`) |
| `tests/js/lib.test.mjs` | create | `node --test` tests for `lib.js` |
| `extension/background.js` | create | Service worker: site toggle, script registration, queue, fetching, server calls |
| `extension/content.js` | create | Image detection, requests, swapping, badges, originals |
| `extension/popup.html`, `popup.css`, `popup.js` | create | Popup UI |
| `tests/extension-page/make_page.py` | create | Generates the manual test page |
| `.gitignore` | modify | Ignore generated test page files |
| `README.md` | modify | Add a "Browser extension" section |

## Message protocol (content ↔ service worker, over a port named `manga-colorizer`)

- Content → worker: `{ type: "colorize", id: number, url: string, pageUrl: string, preset: string, distance: number, bytes?: string /* base64 */ }`
- Content → worker: `{ type: "priority", items: Array<[id: number, distance: number]> }`
- Content → worker: `{ type: "cancel", id: number }`
- Worker → content: `{ type: "result", id: number, status: "done" | "skipped" | "failed" | "offline", jpegBase64?: string }`
- Popup → worker (`runtime.sendMessage`): `{ type: "set-site", origin: string, enabled: boolean, tabId: number }` → reply `{ ok: true } | { ok: false, error: string }`
- `chrome.storage.local` keys: `enabledOrigins: string[]` (default `[]`), `preset: string` (default `"natural"`), `showOriginals: boolean` (default `false`).

---

### Task 1: `POST /api/colorize` endpoint

**Files:**
- Modify: `server.py`: constants after `NO_STORE` (line 26), the endpoint after `health()` (lines 163-165), `_open_image` after `_jpeg()`
- Modify: `pyproject.toml` (dev group)
- Test: `tests/test_colorize_endpoint.py`

**Interfaces:**
- Consumes: the existing `Settings` (query params via `Depends()`), `model_status`, `_get_colorizer()`, `_GpuLockedColorizer`, `NO_STORE`.
- Produces: `POST /api/colorize`, a multipart field `file`, and query params `size, denoise, skip_colored, saturation, warmth, hue, clean_whites`. Responses are as in Global Constraints. Module constants: `MAX_UPLOAD_BYTES`, `MAX_PIXELS`, `MIN_COLORIZE_SIDE`, `COLORIZE_JPEG_QUALITY`.

- [ ] **Step 1: Add httpx to the dev dependencies**

Run: `cd ~/manga-colorizer-repo && uv add --dev httpx`
Expected: `pyproject.toml` dev group becomes `dev = ["httpx>=…", "pytest"]` and `uv.lock` updates.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_colorize_endpoint.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server


class FakeColorizer:
    device = "cpu"

    def __init__(self):
        self.already_colored = False
        self.calls = []

    def is_already_colored(self, image):
        return self.already_colored

    def colorize(self, image, size=576, denoise_sigma=25, tone=None):
        self.calls.append({"size": size, "denoise_sigma": denoise_sigma, "tone": tone})
        return Image.new("RGB", image.size, (200, 90, 80))


@pytest.fixture
def fake(monkeypatch):
    fake = FakeColorizer()
    monkeypatch.setattr(server, "_colorizer", fake)
    monkeypatch.setitem(server.model_status, "state", "ready")
    return fake


@pytest.fixture
def client():
    # No `with` block: that would run the lifespan (wipes data/ and loads the real model).
    return TestClient(server.app)


def image_bytes(size=(600, 900), mode="L", fmt="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size).save(buffer, fmt)
    return buffer.getvalue()


def post(client, data: bytes, **params):
    return client.post("/api/colorize", params=params,
                       files={"file": ("page", data, "application/octet-stream")})


def test_returns_colored_jpeg_at_original_size(client, fake):
    response = post(client, image_bytes((600, 900)))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).size == (600, 900)


def test_passes_tone_settings_and_defaults_through(client, fake):
    post(client, image_bytes(), saturation=1.6, warmth=0.05, clean_whites=0.7)
    call = fake.calls[0]
    assert (call["tone"].saturation, call["tone"].warmth, call["tone"].clean_whites) == (1.6, 0.05, 0.7)
    assert (call["size"], call["denoise_sigma"]) == (576, 25)


@pytest.mark.parametrize("mode", ["L", "LA", "P", "RGB", "RGBA"])
def test_accepts_common_image_modes(client, fake, mode):
    assert post(client, image_bytes(mode=mode)).status_code == 200


def test_already_colored_returns_204(client, fake):
    fake.already_colored = True
    response = post(client, image_bytes(mode="RGB"))
    assert response.status_code == 204
    assert response.content == b""
    assert fake.calls == []


def test_skip_colored_false_colors_anyway(client, fake):
    fake.already_colored = True
    assert post(client, image_bytes(mode="RGB"), skip_colored=False).status_code == 200


def test_too_small_returns_204(client, fake):
    response = post(client, image_bytes((200, 900)))
    assert response.status_code == 204
    assert fake.calls == []


def test_unreadable_image_returns_422(client, fake):
    assert post(client, b"not an image").status_code == 422


def test_truncated_image_returns_422(client, fake):
    jpeg = image_bytes(fmt="JPEG")
    assert post(client, jpeg[: len(jpeg) // 2]).status_code == 422


def test_oversized_upload_returns_413(client, fake, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1000)
    assert post(client, b"\0" * 2000).status_code == 413


def test_too_many_pixels_returns_413(client, fake, monkeypatch):
    monkeypatch.setattr(server, "MAX_PIXELS", 600 * 900 - 1)
    assert post(client, image_bytes((600, 900))).status_code == 413


def test_model_loading_returns_503(client, fake, monkeypatch):
    monkeypatch.setitem(server.model_status, "state", "loading")
    response = post(client, image_bytes())
    assert response.status_code == 503
    assert fake.calls == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ~/manga-colorizer-repo && uv run pytest tests/test_colorize_endpoint.py -v`
Expected: FAIL. Every test gets `404` instead of the expected status (the route doesn't exist), apart from the `monkeypatch.setattr` tests, which fail with `AttributeError: <module 'server'> has no attribute 'MAX_UPLOAD_BYTES'`.

- [ ] **Step 4: Implement the endpoint**

In `server.py`, below `NO_STORE = {"Cache-Control": "no-store"}` add:

```python
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 40_000_000
MIN_COLORIZE_SIDE = 256  # the model needs at least this much on the short side
COLORIZE_JPEG_QUALITY = 90
```

Directly after the `health()` endpoint add:

```python
@app.post("/api/colorize")
def colorize_image(file: UploadFile = File(...), settings: Settings = Depends()) -> Response:
    """One page image in, the colored page out. Used by the browser extension."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image is larger than 25 MB")
    image = _open_image(data)
    if model_status["state"] != "ready":
        raise HTTPException(503, model_status["error"] or "Model is still loading")
    colorizer = _GpuLockedColorizer(_get_colorizer())
    options = settings.to_options()
    if min(image.size) < MIN_COLORIZE_SIDE or (options.skip_colored and colorizer.is_already_colored(image)):
        return Response(status_code=204)
    colored = colorizer.colorize(image, size=options.size, denoise_sigma=options.denoise_sigma, tone=options.tone)
    buffer = io.BytesIO()
    colored.convert("RGB").save(buffer, "JPEG", quality=COLORIZE_JPEG_QUALITY)
    return Response(buffer.getvalue(), media_type="image/jpeg", headers=NO_STORE)
```

Directly after `_jpeg()` add:

```python
def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise HTTPException(413, "Image has too many pixels")
    except Exception:
        raise HTTPException(422, "Not a readable image")
    if image.width * image.height > MAX_PIXELS:
        raise HTTPException(413, "Image is larger than 40 megapixels")
    try:
        image.load()
    except Exception:
        raise HTTPException(422, "Not a readable image")
    return image
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/manga-colorizer-repo && uv run pytest tests/test_colorize_endpoint.py -v`
Expected: all 15 pass (11 functions, one of them parametrized ×5).

Then the whole suite: `uv run pytest`
Expected: all pass (the existing `test_books.py` and `test_tone.py` are unchanged).

- [ ] **Step 6: Smoke test against the real model**

Start the server (`./run.sh`) if it isn't running and wait for `curl -s http://127.0.0.1:7860/api/health` to show `"state":"ready"`. Then:

```bash
cd ~/manga-colorizer-repo && .venv/bin/python -c "
from PIL import Image, ImageDraw
img = Image.new('L', (800, 1200), 255); d = ImageDraw.Draw(img)
d.ellipse([200, 300, 600, 700], outline=0, width=8); img.save('/tmp/claude-smoke.png')"
curl -s -o /tmp/claude-smoke-out.jpg -w "%{http_code} %{content_type}\n" \
  -F file=@/tmp/claude-smoke.png "http://127.0.0.1:7860/api/colorize?saturation=1.6"
```
Expected: `200 image/jpeg`. The server must be restarted after editing `server.py` (uvicorn isn't running with `--reload`).

- [ ] **Step 7: Stop. No commit, the user commits.**

---

### Task 2: Pure helpers (`extension/lib.js`) with node tests

**Files:**
- Create: `extension/lib.js`
- Test: `tests/js/lib.test.mjs`

**Interfaces:**
- Produces:
  - `class JobQueue`. A job is `{ key: string, payload: object, waiters: Map<string, number> }` (waiter key → distance). Methods:
    - `add(key, payload, waiterKey, distance)`: creates the job or merges into it.
    - `setDistance(waiterKey, distance)`
    - `removeWaitersWhere(predicate: (waiterKey) => boolean)`: a job with no waiters left is dropped.
    - `removeJobsWhere(predicate: (job) => boolean)`
    - `pop(): job | null`: the lowest min-distance; insertion order breaks ties.
    - `requeue(job)`: re-adds a popped job with its current waiters; a job with no waiters is dropped.
    - `clear()`
    - getter `size`
  - `class LruCache(capacity)`: `get(key)`, `set(key, value)`, `clear()`, getter `size`.
  - `bytesToBase64(bytes: Uint8Array): string`, `base64ToBytes(base64: string): Uint8Array`
  - `refererRuleId(host: string): number`: stable, an integer from 1 to 2_000_000_000.

- [ ] **Step 1: Write the failing tests**

Create `tests/js/lib.test.mjs`:

```js
import assert from "node:assert/strict";
import { test } from "node:test";

import { JobQueue, LruCache, base64ToBytes, bytesToBase64, refererRuleId } from "../../extension/lib.js";

test("pop returns the job closest to the viewport", () => {
  const queue = new JobQueue();
  queue.add("far", {}, "1:1", 900);
  queue.add("near", {}, "1:2", 10);
  queue.add("mid", {}, "1:3", 300);
  assert.deepEqual([queue.pop().key, queue.pop().key, queue.pop().key], ["near", "mid", "far"]);
  assert.equal(queue.pop(), null);
});

test("ties keep insertion order", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 0);
  queue.add("b", {}, "1:2", 0);
  assert.equal(queue.pop().key, "a");
});

test("same key merges waiters and uses the smallest distance", () => {
  const queue = new JobQueue();
  queue.add("page", { url: "x" }, "1:1", 800);
  queue.add("other", {}, "1:2", 100);
  queue.add("page", { url: "x" }, "2:1", 5);
  assert.equal(queue.size, 2);
  const job = queue.pop();
  assert.equal(job.key, "page");
  assert.deepEqual([...job.waiters.keys()], ["1:1", "2:1"]);
});

test("setDistance reorders", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 10);
  queue.add("b", {}, "1:2", 20);
  queue.setDistance("1:2", 1);
  assert.equal(queue.pop().key, "b");
});

test("removing the last waiter drops the job", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 10);
  queue.add("a", {}, "2:1", 10);
  queue.add("b", {}, "1:2", 20);
  queue.removeWaitersWhere((waiterKey) => waiterKey.startsWith("1:"));
  assert.equal(queue.size, 1);
  assert.deepEqual([...queue.pop().waiters.keys()], ["2:1"]);
});

test("removeJobsWhere drops matching jobs", () => {
  const queue = new JobQueue();
  queue.add("a", { preset: "natural" }, "1:1", 0);
  queue.add("b", { preset: "vivid" }, "1:2", 0);
  queue.removeJobsWhere((job) => job.payload.preset !== "vivid");
  assert.equal(queue.pop().key, "b");
  assert.equal(queue.size, 0);
});

test("requeue puts a popped job back with its waiters", () => {
  const queue = new JobQueue();
  queue.add("a", { url: "x" }, "1:1", 3);
  const job = queue.pop();
  queue.requeue(job);
  assert.equal(queue.size, 1);
  queue.setDistance("1:1", 0);
  assert.deepEqual(queue.pop().payload, { url: "x" });
});

test("requeue of a job whose waiters all left is a no-op", () => {
  const queue = new JobQueue();
  queue.add("a", {}, "1:1", 3);
  const job = queue.pop();
  job.waiters.clear();
  queue.requeue(job);
  assert.equal(queue.size, 0);
});

test("LruCache evicts the least recently used entry", () => {
  const cache = new LruCache(2);
  cache.set("a", 1);
  cache.set("b", 2);
  assert.equal(cache.get("a"), 1); // "a" is now most recent
  cache.set("c", 3);
  assert.equal(cache.get("b"), undefined);
  assert.equal(cache.get("a"), 1);
  assert.equal(cache.get("c"), 3);
  assert.equal(cache.size, 2);
});

test("base64 round-trips large binary data", () => {
  const bytes = new Uint8Array(200_000);
  for (let index = 0; index < bytes.length; index++) bytes[index] = (index * 31) % 256;
  const encoded = bytesToBase64(bytes);
  assert.equal(encoded, Buffer.from(bytes).toString("base64"));
  assert.deepEqual(base64ToBytes(encoded), bytes);
});

test("refererRuleId is stable and in range", () => {
  const id = refererRuleId("cdn.example.com");
  assert.equal(id, refererRuleId("cdn.example.com"));
  assert.notEqual(id, refererRuleId("img.example.com"));
  assert.ok(Number.isInteger(id) && id >= 1 && id <= 2_000_000_000);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/manga-colorizer-repo && node --test tests/js/`
Expected: FAIL with `Cannot find module '…/extension/lib.js'`.

- [ ] **Step 3: Implement `extension/lib.js`**

```js
// Pure helpers for the service worker. No chrome.* here, so they run under `node --test`.

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/manga-colorizer-repo && node --test tests/js/`
Expected: 11 tests, all pass. (Node 24 detects ES module syntax in `lib.js` on its own; no `package.json` is needed.)

- [ ] **Step 5: Stop. No commit, the user commits.**

---

### Task 3: Manifest, config and service worker

**Files:**
- Create: `extension/manifest.json`, `extension/config.js`, `extension/background.js`

**Interfaces:**
- Consumes: everything from Task 2 (`JobQueue`, `LruCache`, `bytesToBase64`, `base64ToBytes`, `refererRuleId`); `POST /api/colorize` from Task 1.
- Produces: `config.js` exports `SERVER_URL`, `DEFAULT_PRESET`, and `PRESETS` (`{ [name]: { label: string, tone: { saturation, warmth, hue, clean_whites } } }`). The service worker side of the message protocol (see the top of the plan): the port name `manga-colorizer`, the `set-site` runtime message, and the registered content script id `manga-colorizer`, which injects `content.js` on `${origin}/*` for every enabled origin.

- [ ] **Step 1: Create `extension/manifest.json`**

```json
{
  "manifest_version": 3,
  "name": "Manga Colorizer",
  "version": "0.1.0",
  "description": "Colorizes black-and-white manga pages in place using your local Manga Colorizer server.",
  "permissions": ["storage", "scripting", "declarativeNetRequest"],
  "host_permissions": ["<all_urls>"],
  "background": { "service_worker": "background.js", "type": "module" },
  "action": { "default_popup": "popup.html", "default_title": "Manga Colorizer" }
}
```

- [ ] **Step 2: Create `extension/config.js`**

```js
export const SERVER_URL = "http://127.0.0.1:7860";
export const DEFAULT_PRESET = "natural";

// Same values as PRESETS in static/index.html; keep the two in sync.
export const PRESETS = {
  natural: { label: "Natural", tone: { saturation: 1, warmth: 0, hue: 0, clean_whites: 0.5 } },
  vivid: { label: "Vivid", tone: { saturation: 1.6, warmth: 0.05, hue: 0, clean_whites: 0.7 } },
  soft: { label: "Soft", tone: { saturation: 0.7, warmth: 0.1, hue: 0, clean_whites: 0.6 } },
  warm: { label: "Warm vintage", tone: { saturation: 0.9, warmth: 0.55, hue: 0, clean_whites: 0.2 } },
  cool: { label: "Cool", tone: { saturation: 1.1, warmth: -0.4, hue: 0, clean_whites: 0.6 } },
};
```

- [ ] **Step 3: Create `extension/background.js`**

```js
// Service worker: per-site toggle, content script registration, and the colorize queue.
// Page images are downloaded here (host permissions avoid CORS) and sent to the local server.
import { DEFAULT_PRESET, PRESETS, SERVER_URL } from "./config.js";
import { JobQueue, LruCache, base64ToBytes, bytesToBase64, refererRuleId } from "./lib.js";

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
  if (area === "local" && changes.preset) {
    const preset = changes.preset.newValue ?? DEFAULT_PRESET;
    queue.removeJobsWhere((job) => job.payload.preset !== preset);
  }
});

async function restoreContentScripts() {
  const { enabledOrigins = [] } = await chrome.storage.local.get("enabledOrigins");
  await syncContentScripts(enabledOrigins);
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
    const preset = PRESETS[message.preset] ? message.preset : DEFAULT_PRESET;
    const key = `${preset} ${message.url}`;
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
    const payload = { url: message.url, pageUrl: message.pageUrl, preset, bytes: message.bytes ?? null };
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
        queue.requeue(job);
      } else if (result.status !== "failed") {
        cache.set(job.key, result);
      }
    }
  } finally {
    busy = false;
  }
}

async function processJob({ url, pageUrl, preset, bytes }) {
  try {
    const image = bytes ? new Blob([base64ToBytes(bytes)]) : await fetchImage(url, pageUrl);
    return await colorizeOnServer(image, preset);
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

async function colorizeOnServer(image, preset) {
  const form = new FormData();
  form.append("file", image, "page");
  const params = new URLSearchParams(PRESETS[preset].tone);
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
```

- [ ] **Step 4: Syntax check**

Run: `cd ~/manga-colorizer-repo && node --check extension/config.js && node --check extension/background.js && node --check extension/lib.js && echo OK`
Expected: `OK`.

- [ ] **Step 5: Load in Brave and check the service worker**

1. Open `brave://extensions`, turn on **Developer mode** (top right), click **Load unpacked**, and choose `~/manga-colorizer-repo/extension`. The popup files don't exist yet, so Brave may warn about `popup.html`. That's expected until Task 5.
2. Click **service worker** on the extension card to open its DevTools console. Expected: no errors.
3. In that console run: `await chrome.scripting.getRegisteredContentScripts()`
   Expected: `[]`.

- [ ] **Step 6: Stop. No commit, the user commits.**

---

### Task 4: Content script

**Files:**
- Create: `extension/content.js`

**Interfaces:**
- Consumes: the port protocol and storage keys from the top of the plan (served by Task 3).
- Produces: a classic script, safe to inject twice (guarded by `window.__mangaColorizer`). It stops itself and restores originals when its origin is removed from `enabledOrigins`.

- [ ] **Step 1: Create `extension/content.js`**

```js
// Injected only on sites turned on in the popup. Finds manga page images, asks the
// service worker to colorize them, and swaps the colored versions in place.
(() => {
  if (window.__mangaColorizer) {
    return;
  }
  window.__mangaColorizer = true;

  const PORT_NAME = "manga-colorizer";
  const DEFAULT_PRESET = "natural";
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
  const intersection = new IntersectionObserver(onIntersect, { rootMargin: `${LOOKAHEAD_PX}px 0px` });
  const mutations = new MutationObserver(onMutations);
  let nextId = 1;
  let port = null;
  let preset = DEFAULT_PRESET;
  let showOriginals = false;
  let active = true;
  let tickTimer = 0;

  start();

  async function start() {
    const stored = await chrome.storage.local.get(["enabledOrigins", "preset", "showOriginals"]);
    if (!(stored.enabledOrigins ?? []).includes(location.origin)) {
      active = false;
      window.__mangaColorizer = false;
      return;
    }
    preset = stored.preset ?? DEFAULT_PRESET;
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
      preset, distance: distanceOf(state.img),
    };
    if (/^(blob|data):/.test(state.sourceUrl)) {
      try {
        message.bytes = await readBase64(state.sourceUrl);
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
      stop(); // the extension was reloaded or removed; this copy is orphaned
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
    setTimeout(() => {
      for (const state of byId.values()) {
        if (state.status === "pending" && state.message) {
          send(state.message);
        }
      }
    }, RECONNECT_DELAY_MS);
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
    if (changes.preset) {
      preset = changes.preset.newValue ?? DEFAULT_PRESET;
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

  async function readBase64(url) {
    const blob = await (await fetch(url)).blob();
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
```

- [ ] **Step 2: Syntax check**

Run: `cd ~/manga-colorizer-repo && node --check extension/content.js && echo OK`
Expected: `OK`.

- [ ] **Step 3: Stop. No commit, the user commits.** (The behavior is verified end to end in Task 6, once the popup exists to turn a site on.)

---

### Task 5: Popup

**Files:**
- Create: `extension/popup.html`, `extension/popup.css`, `extension/popup.js`

**Interfaces:**
- Consumes: `SERVER_URL`, `DEFAULT_PRESET`, `PRESETS` from `config.js`; the `set-site` message (Task 3); `GET /api/health`, which returns `{ state: "loading" | "ready" | "error", device: string, error: string }`.
- Produces: writes the `preset` and `showOriginals` storage keys.

- [ ] **Step 1: Create `extension/popup.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Manga Colorizer</title>
  <link rel="stylesheet" href="popup.css">
  <script type="module" src="popup.js"></script>
</head>
<body>
  <h1>Manga Colorizer</h1>
  <p id="server" class="status">Checking server…</p>
  <label class="row"><input type="checkbox" id="site"> <span>Colorize on <strong id="origin"></strong></span></label>
  <p id="note" class="note" hidden></p>
  <label class="row">Look <select id="preset"></select></label>
  <label class="row"><input type="checkbox" id="originals"> Show originals</label>
</body>
</html>
```

- [ ] **Step 2: Create `extension/popup.css`**

```css
:root {
  --bg: #ffffff;
  --fg: #1d1d1f;
  --muted: #6b6b70;
  --ok: #1a7f37;
  --wait: #9a6700;
  --error: #c62828;
  color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1f1f22;
    --fg: #ececf0;
    --muted: #a0a0a8;
    --ok: #4ac26b;
    --wait: #d4a72c;
    --error: #ff7b72;
  }
}

body {
  width: 280px;
  margin: 0;
  padding: 12px 14px;
  background: var(--bg);
  color: var(--fg);
  font: 13px/1.4 system-ui, sans-serif;
}

h1 {
  margin: 0 0 6px;
  font-size: 15px;
}

.status {
  margin: 0 0 10px;
  color: var(--muted);
}

.status.ok { color: var(--ok); }
.status.wait { color: var(--wait); }
.status.error { color: var(--error); }

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
}

.row select {
  margin-left: auto;
}

.note {
  margin: -4px 0 8px;
  color: var(--muted);
  font-size: 12px;
}
```

- [ ] **Step 3: Create `extension/popup.js`**

```js
import { DEFAULT_PRESET, PRESETS, SERVER_URL } from "./config.js";

const HEALTH_TIMEOUT_MS = 2000;
const $ = (id) => document.getElementById(id);

init();

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const stored = await chrome.storage.local.get(["enabledOrigins", "preset", "showOriginals"]);
  setUpSiteToggle(tab, stored.enabledOrigins ?? []);
  setUpPreset(stored.preset ?? DEFAULT_PRESET);
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

function setUpPreset(current) {
  const select = $("preset");
  for (const [name, preset] of Object.entries(PRESETS)) {
    select.append(new Option(preset.label, name, false, name === current));
  }
  select.addEventListener("change", () => chrome.storage.local.set({ preset: select.value }));
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
    setStatus("error", "Server offline. Run ./run.sh in manga-colorizer-repo.");
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
```

- [ ] **Step 4: Check it in Brave**

1. `node --check extension/popup.js && echo OK` → `OK`.
2. On `brave://extensions`, click the reload icon on the Manga Colorizer card.
3. With the server running, open any `https://` page and click the extension icon (pin it from the puzzle-piece menu). Expected: "Server ready · GPU (NVIDIA GeForce RTX 3060 Ti)", "Colorize on <host>" unchecked, Look = Natural, Show originals unchecked.
4. Tick "Colorize on …", then run `await chrome.scripting.getRegisteredContentScripts()` in the service worker console. Expected: one entry with `matches: ["https://<host>/*"]`. Untick it again → `[]`.
5. Stop the server and reopen the popup. Expected: the red "Server offline. Run ./run.sh in manga-colorizer-repo."

- [ ] **Step 5: Stop. No commit, the user commits.**

---

### Task 6: Test page, end-to-end check, README

**Files:**
- Create: `tests/extension-page/make_page.py`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Consumes: the whole extension (Tasks 3-5) and the endpoint (Task 1).
- Produces: `tests/extension-page/index.html` plus the PNGs (generated, git-ignored), served on `http://127.0.0.1:8765/`.

- [ ] **Step 1: Create `tests/extension-page/make_page.py`**

```python
"""Writes a local test page for the browser extension.

Synthetic grayscale "manga" pages, plus the cases the extension must handle: lazy loading
through data-src, native loading="lazy", <picture>, a blob: URL, a small icon (ignored) and a
color image (left as is).

    .venv/bin/python tests/extension-page/make_page.py
    .venv/bin/python -m http.server 8765 -d tests/extension-page
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).parent
PAGE_SIZE = (800, 1200)
PAGE_COUNT = 7
PANEL_ROWS = 3
PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAAAAACw="


def make_page(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("L", PAGE_SIZE, 255)
    draw = ImageDraw.Draw(image)
    panel_height = (PAGE_SIZE[1] - 40) // PANEL_ROWS
    for row in range(PANEL_ROWS):
        top = 20 + row * panel_height
        bottom = top + panel_height - 20
        draw.rectangle([20, top, PAGE_SIZE[0] - 20, bottom], outline=0, width=6)
        for _ in range(4):
            x = int(rng.integers(90, PAGE_SIZE[0] - 90))
            y = int(rng.integers(top + 80, bottom - 80))
            radius = int(rng.integers(30, 70))
            draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                         outline=0, width=4, fill=int(rng.integers(150, 250)))
        for x in range(30, PAGE_SIZE[0] - 30, 10):  # screentone strip
            for y in range(bottom - 60, bottom - 10, 10):
                draw.ellipse([x, y, x + 3, y + 3], fill=90)
    return image


def make_color_image() -> Image.Image:
    gradient = np.zeros((600, 800, 3), np.uint8)
    gradient[..., 0] = np.linspace(40, 230, 800, dtype=np.uint8)
    gradient[..., 2] = np.linspace(230, 40, 600, dtype=np.uint8)[:, None]
    return Image.fromarray(gradient)


def main() -> None:
    for index in range(1, PAGE_COUNT + 1):
        make_page(index).save(OUT / f"page-{index}.png")
    Image.new("L", (64, 64), 0).save(OUT / "icon.png")
    make_color_image().save(OUT / "color.png")
    (OUT / "index.html").write_text(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Extension test page</title>
<style>
  body {{ margin: 0; background: #111; color: #ddd; font: 14px system-ui, sans-serif; }}
  main {{ max-width: 800px; margin: 0 auto; }}
  h2 {{ font-size: 14px; margin: 24px 8px 6px; }}
  img {{ display: block; width: 100%; height: auto; }}
</style>
</head>
<body>
<main>
  <h2><img src="icon.png" alt="" style="width:32px;display:inline"> Icon above should stay black</h2>
  <h2>1. Plain img</h2><img src="page-1.png" alt="">
  <h2>2. Plain img</h2><img src="page-2.png" alt="">
  <h2>3. Color image, should stay as is</h2><img src="color.png" alt="">
  <h2>4. Lazy through data-src</h2><img src="{PLACEHOLDER}" data-src="page-3.png" alt="" width="800" height="1200">
  <h2>5. Lazy through data-src</h2><img src="{PLACEHOLDER}" data-src="page-4.png" alt="" width="800" height="1200">
  <h2>6. Native loading=lazy</h2><img src="page-5.png" loading="lazy" alt="" width="800" height="1200">
  <h2>7. Inside picture</h2><picture><source srcset="page-6.png"><img src="page-1.png" alt=""></picture>
  <h2>8. blob: URL</h2><img id="blob" alt="" width="800" height="1200">
</main>
<script>
  // Lazy loader like many reader sites: the real URL sits in data-src until near the viewport.
  const lazy = new IntersectionObserver((entries) => {{
    for (const entry of entries) {{
      if (entry.isIntersecting) {{
        entry.target.src = entry.target.dataset.src;
        lazy.unobserve(entry.target);
      }}
    }}
  }}, {{ rootMargin: "200px" }});
  document.querySelectorAll("img[data-src]").forEach((img) => lazy.observe(img));
  // Reader that decodes pages into blob: URLs.
  fetch("page-7.png").then((response) => response.blob()).then((blob) => {{
    document.getElementById("blob").src = URL.createObjectURL(blob);
  }});
</script>
</body>
</html>
""")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ignore the generated files**

Append to `.gitignore`:

```
tests/extension-page/*.png
tests/extension-page/index.html
```

- [ ] **Step 3: Generate and serve the page**

Run: `cd ~/manga-colorizer-repo && .venv/bin/python tests/extension-page/make_page.py && ls tests/extension-page`
Expected: `color.png icon.png index.html make_page.py page-1.png … page-7.png`.

Serve it (keep this running): `.venv/bin/python -m http.server 8765 -d tests/extension-page`
Make sure the colorizer server is running and `/api/health` reports `ready`.

- [ ] **Step 4: End-to-end check in Brave**

Reload the extension on `brave://extensions` and open `http://127.0.0.1:8765/`. Click the extension icon and tick "Colorize on 127.0.0.1:8765". Expected, in order:

1. Pages 1 and 2 show a "coloring…" badge and switch to color within a few seconds. The badges disappear.
2. The icon and the color image (3) get no badge and don't change.
3. Scrolling down: 4 and 5 (data-src), 6 (native lazy), 7 (picture) and 8 (blob) all switch to color. Each is colored before or shortly after it reaches the screen.
4. Popup → tick **Show originals**: every page goes back to black and white instantly, with no badges. Untick it: color returns instantly (from blob URLs, without server calls; the terminal running `./run.sh` shows no new `POST /api/colorize` lines).
5. Popup → Look: **Vivid**: the visible pages get badges again and recolor with stronger color.
6. Stop the server (Ctrl+C in its terminal) and reload the tab. Expected: the pages show "server offline" badges, stay black and white, and nothing breaks. Start `./run.sh` again: within about 5 s of the model being ready, the pages color without reloading the tab.
7. Reload the extension on `brave://extensions` with the tab open, then scroll: nothing breaks on the page. Reload the tab: coloring works again (the registered content script survives extension reloads through `onInstalled`).
8. Popup → untick "Colorize on 127.0.0.1:8765": every page returns to the original and the badges go away.
9. The service worker console (`brave://extensions` → "service worker") shows no uncaught errors.

If an item fails, stop and debug it (superpowers:systematic-debugging) before going on.

- [ ] **Step 5: Add a README section**

In `README.md`, add after the "## Run" section:

```markdown
## Browser extension

Colorizes manga pages in place while you read on a website. It uses this server, so keep `./run.sh` running.

1. Open `brave://extensions` (or `chrome://extensions`), turn on **Developer mode**, click **Load unpacked** and pick the `extension/` folder.
2. Pin the extension, open a chapter, click the icon and tick **Colorize on <site>**. The site is remembered.
3. Pages are colored a little before they scroll into view. Use **Look** to pick a preset and **Show originals** to flip back to black and white.

Colored pages live only in memory; nothing is saved. Pages drawn into a `<canvas>` or used as CSS backgrounds aren't supported.

To try it locally: `.venv/bin/python tests/extension-page/make_page.py`, then `.venv/bin/python -m http.server 8765 -d tests/extension-page`, and open http://127.0.0.1:8765/.
```

- [ ] **Step 6: Final checks**

Run: `cd ~/manga-colorizer-repo && uv run pytest && node --test tests/js/`
Expected: all Python tests and all 11 JS tests pass.

- [ ] **Step 7: Stop. No commit, the user commits.**
