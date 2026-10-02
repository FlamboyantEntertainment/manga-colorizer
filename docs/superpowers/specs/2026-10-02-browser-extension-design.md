# Browser extension: colorize manga pages in place

> Update: after this spec, the popup gained every web UI setting (detail, denoise, tone sliders, keep already-colored pages), not just the presets. See CHANGELOG.md for 0.2.0.

## Goal

While reading a chapter on a website in Brave, black-and-white page images turn colored as you scroll, with no upload step. Colorization runs on the local server (`./run.sh`, `http://127.0.0.1:7860`) with the same model and tone settings as the book workflow.

Success looks like this: turn the extension on for a site, open a chapter, and pages switch to color shortly before they scroll into view. Scrolling back up does not recolor them, and a switch flips back to the originals.

## Decisions

- **Browser:** Brave (Chromium), Manifest V3, loaded unpacked from `extension/`.
- **Trigger:** a toolbar popup turns the extension on or off per site (origin). The choice is remembered.
- **Where inference runs:** only on the local server. No cloud, no in-browser model.
- **Storage:** colored pages are kept in memory only (the tab's blob URLs, plus a small in-memory cache in the service worker). Nothing is written to disk.
- **Look:** the extension offers the same five presets as the web UI (Natural, Vivid, Soft, Warm vintage, Cool). Detail and Denoise use the server defaults (576, 25).

## Server change: `POST /api/colorize`

The request is multipart, with one field `file` holding an image (JPEG, PNG or WebP). The tone settings go in the query string and reuse the existing `Settings` model through `Depends()`, the same way `/api/jobs/{id}/sample/colored` does.

| Case | Response |
|---|---|
| Colored successfully | `200`, `image/jpeg` (quality 90), same pixel size as the input |
| Image already in color (`skip_colored` and `is_already_colored`), or shorter than 256 px on its short side | `204`, empty body |
| Model still loading, or failed to load | `503` with the model status message |
| Not a readable image | `422` |
| Larger than 25 MB, or more than 40 megapixels | `413` |

- Inference goes through `_GpuLockedColorizer`, so it takes turns with book jobs and previews on the one GPU.
- It's a plain `def` endpoint, so FastAPI runs it in its thread pool.
- No CORS change is needed. The extension's service worker has host permission for `http://127.0.0.1:7860/*`, so its requests aren't subject to CORS.

## Extension layout (`extension/`)

| File | Responsibility |
|---|---|
| `manifest.json` | MV3. Permissions: `storage`, `scripting`, `declarativeNetRequest`. Host permissions: `<all_urls>` (see below). The service worker is `background.js`, an ES module. |
| `config.js` | The server URL and the five tone presets (a copy of `PRESETS` in `static/index.html`). Imported by the popup and background. |
| `lib.js` | Pure helpers with no `chrome.*` calls: the job queue, the LRU cache, base64, and referrer rule ids. Unit-tested with `node --test`. |
| `background.js` | Per-site enable state, content script registration, the colorize queue, image fetching, the referrer rules, and the in-memory cache. |
| `content.js` | Finds page images, asks the background to color them, swaps the results in, and restores originals. |
| `popup.html` / `popup.js` / `popup.css` | Site on/off, preset choice, the "show originals" switch, and server status. |

### Why `<all_urls>`

Chapter images usually come from a CDN on a different domain than the reading site, and that domain isn't known in advance. The service worker needs host permission for the CDN to download images (a content script can't read their pixels across origins). The extension is a personal unpacked one, so it gets broad host permission once. Whether it does anything on a site is controlled by the per-site toggle: content scripts are registered only for sites you've turned on.

## Data flow

1. **Popup on:** the popup sends `setSite(origin, true)`. The background saves it in `chrome.storage.local` and calls `chrome.scripting.registerContentScripts` for `origin/*` (persisted across restarts). It also injects into the current tab right away.
2. **Content script:**
   - Watches `<img>` elements: the ones present at load, and new ones and `src`/`srcset` changes through a `MutationObserver`.
   - An image counts as a candidate once it's loaded with `naturalWidth` ≥ 500 and `naturalHeight` ≥ 500.
   - An `IntersectionObserver` with a 1500 px root margin sends `colorize(url, pageUrl, distance)` for candidates as they approach the viewport.
3. **Background queue:**
   - A priority queue ordered by distance from the viewport, with one request in flight at a time.
   - Requests for the same URL are merged.
   - When a tab navigates or closes, its pending entries are dropped.
4. **Fetch:**
   - Before downloading from a new image host, the background adds a session `declarativeNetRequest` rule that sets `Referer` to the reading page's origin. The rule applies only to requests the extension itself starts. Many image CDNs reject requests without the site as referrer.
   - The background then downloads the image and posts it to `/api/colorize` with the current preset.
5. **Result:**
   - The response bytes go back to the content script as a base64 data URL (extension messaging can't carry a Blob).
   - The content script turns it into a blob URL and saves the original `src`/`srcset` in `data-mc-*` attributes.
   - It then sets the new `src`, removes `srcset`, and marks the image so the `MutationObserver` ignores this change.
   - A `204` leaves the image alone and marks it as done.
6. **Cache:**
   - The background keeps an LRU of the last 40 results, keyed by URL plus preset.
   - It's best effort: it's lost when the service worker stops, which is fine because the tab keeps its own swapped images.
7. **Preset change:** the cache is cleared, each enabled tab restores its originals, and the visible images go through the queue again.
8. **Show originals:** the content script swaps the stored originals back in, or the colored ones back again. No server calls.

## Status and errors

- **Badge:** a small label in the image's corner shows "coloring…" while an image waits or is being processed. It's removed when done.
- **Server down:** a fetch error or `503` stops the queue for 5 s and labels the images "server offline". The popup shows server status from `/api/health` and the hint "run ./run.sh in manga-colorizer-repo".
- **One image fails** (download failure, `422`, `413`): that image keeps the original with no label, and the queue continues.
- **Site sets `src` again** (lazy loaders): the image is treated as new and goes through the queue again.

## Testing

- **pytest `tests/test_colorize_endpoint.py`:** uses FastAPI's `TestClient` with a fake colorizer patched into `server._colorizer`. Checks the 200 JPEG and its size, the 204 for already-colored images, the 422 for bad input and the 503 while loading. Needs `httpx` added to the dev group.
- **node --test `tests/js/lib.test.mjs`:** the queue order and merging, cache eviction, and base64.
- **Manual:** load the extension unpacked in Brave and open the page from `tests/extension-page/make_page.py` through a local static server. The test page has synthetic grayscale pages and covers the cases the extension must handle: lazy-loaded images, `<picture>`, a `blob:` URL, a small icon and a color image. Check scroll coloring, badges, the show-originals switch, a preset change, and behavior when the server is stopped.

## Out of scope

Canvas-based readers (images drawn into `<canvas>`), CSS background images, Firefox packaging, saving colored pages, and a detail/denoise control in the popup.
