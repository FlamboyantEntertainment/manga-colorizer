# Manga Colorizer

**Drop in a black-and-white manga, get it back in color.** A self-hosted web app that AI-colors whole books (PDF, EPUB or CBZ) and gives you back the same format. It runs on your own GPU, with no account, no cloud and no uploads to anyone.

[![CI](https://github.com/FlamboyantEntertainment/manga-colorizer/actions/workflows/ci.yml/badge.svg)](https://github.com/FlamboyantEntertainment/manga-colorizer/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

![Before and after: a black-and-white manga page and the same page colorized](docs/before-after.jpg)

![Three consecutive manga pages: black-and-white originals on top, the same pages colorized with the Natural preset below](docs/three-pages.jpg)

<p align="center"><img src="docs/screenshot.jpg" alt="The web UI: tone controls on the left, a live before/after preview on the right" width="720"></p>

## Features

- **Whole books in, whole books out.** Supports PDF, EPUB and CBZ. EPUBs stay valid EPUBs, and page order and file names are preserved.
- **Sharp linework.** The AI colors a small copy of each page, then only the color is transferred onto your original full-resolution page. Lines and screentone stay as crisp as the source.
- **Live preview.** Tune the look on any page of the book with a before/after slider before you commit.
- **Tone controls.** Color strength, warmth, hue shift and "clean whites" (removes the tint on paper and speech bubbles), plus presets.
- **Smart skipping.** Already-colored pages such as covers are left alone, and so are small images like icons.
- **Light on hardware.** Runs on 8 GB NVIDIA GPUs at about 0.5 s per page, and also works on CPU (about 10–20 s per page, so a 200-page volume takes about an hour).

## Quick start

You need [Docker](https://docs.docker.com/get-docker/). Then pick one:

### NVIDIA GPU: Windows (Docker Desktop)

```
docker run -d --gpus all -p 7860:7860 -v manga-models:/models --name manga-colorizer --restart unless-stopped ghcr.io/flamboyantentertainment/manga-colorizer:latest
```

### NVIDIA GPU: Linux

First, one-time setup: install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) so Docker can use your GPU. Then run the same command:

```
docker run -d --gpus all -p 7860:7860 -v manga-models:/models --name manga-colorizer --restart unless-stopped ghcr.io/flamboyantentertainment/manga-colorizer:latest
```

### No NVIDIA GPU (any computer, including Mac)

```
docker run -d -p 7860:7860 -v manga-models:/models --name manga-colorizer --restart unless-stopped ghcr.io/flamboyantentertainment/manga-colorizer:cpu
```

Then open **http://localhost:7860**. On the first start the app downloads the AI model (about 130 MB), and the status line at the top tells you when it's ready.

<details>
<summary>Docker Compose</summary>

```
curl -O https://raw.githubusercontent.com/FlamboyantEntertainment/manga-colorizer/main/docker-compose.yml
docker compose up -d
```

For CPU only, edit the file first: use the `:cpu` tag and delete the `deploy:` section.
</details>

**Update:** `docker pull ghcr.io/flamboyantentertainment/manga-colorizer:latest`, then remove the container (`docker rm -f manga-colorizer`) and run the command again. The downloaded model is kept.

## How to use

1. **Drop** one or more books onto the page.
2. **Tune** the look. The app picks a representative page (it skips covers, blank pages and title pages). Use Prev/Next to check other pages. Drag the slider in the middle of the preview to compare before and after.
3. **Colorize**, then **Download** when it's done. The file is named `<name> (colored).<ext>`.

### Settings

| Setting | What it does |
|---|---|
| Presets | Natural, Vivid, Soft, Warm vintage, Cool |
| Color strength | 0% = grayscale, 100% = the AI's own colors, up to 250% |
| Warmth | Shifts colors toward yellow/red (warm) or blue (cool) |
| Hue shift | Rotates all colors, for when the whole palette feels off |
| Clean whites | Removes the color tint the AI leaves on paper and speech bubbles |
| Detail | The resolution the AI works at. 576 is what it was trained on and usually looks best |
| Denoise | Cleans JPEG noise before coloring. Raise it for noisy scans; use 0 for clean digital releases |

**Reset to defaults** restores everything; double-click any slider to reset just that one.

**Presets** on the same page:

![The same panel in the original and with the Natural, Vivid, Soft, Warm vintage and Cool presets](docs/presets.jpg)

**Detail**: higher values give small areas such as collars and braids their own color, at the cost of speed:

![The same panel at Detail 576, 768 and 1024](docs/detail.jpg)

## What to expect

This uses [manga-colorization-v2](https://github.com/qweasdd/manga-colorization-v2), a fast, lightweight model. Results are pleasant, soft, anime-style color, not hand-colored quality. Each page is colored on its own, so a character's hair or clothes may change color between pages. The tone controls help you get a consistent overall look.

A full four-page story with the default settings:

![Four colorized pages of Go Go! Encyclopedia Girls](docs/pages.jpg)

**Page-to-page consistency**, up close: the same girl is golden blonde on page 1, but in page 2's big close-up her hair turns peach with pink strands. In page 2's smaller panels she's blonde again, so colors can drift even within a page, most often in large close-ups.

![Page 1 and page 2 side by side, with zoomed crops of the same character showing her hair shift from golden blonde to peach-pink](docs/consistency.jpg)

## Troubleshooting

- **The status line says "CPU" but I have an NVIDIA GPU.** Make sure you used `--gpus all`. On Linux, install the NVIDIA Container Toolkit. Check with `docker run --rm --gpus all ubuntu nvidia-smi`.
- **The model failed to download.** The first start needs internet access to GitHub. Restart the container to retry.
- **Port 7860 is already in use.** Change the first number, for example `-p 8080:7860`, then open http://localhost:8080.

## Run without Docker

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it for you):

```
git clone https://github.com/FlamboyantEntertainment/manga-colorizer.git
cd manga-colorizer
./run.sh
```

For CPU-only machines, change the PyTorch index URL in `pyproject.toml` to `https://download.pytorch.org/whl/cpu` first. Run the tests with `uv run pytest`.

## Credits and license

- The colorization model and network code are [manga-colorization-v2](https://github.com/qweasdd/manga-colorization-v2) by qweasdd. The weights are downloaded on first run from the mirror published by [manga-image-translator](https://github.com/zyddnys/manga-image-translator); they are not included in this repository or the Docker images.
- The network code in `mc2/` was adapted from manga-image-translator (GPL-3.0).
- This project is licensed under the [GNU GPL v3.0](LICENSE).
- The demo images in `docs/` are colorized versions of [*Go Go! Encyclopedia Girls*](https://commons.wikimedia.org/wiki/File:Go_Go!_Encyclopedia_Girls_-_English_01.png) (pages 1–4), art by Kasuga, English version by Masatami, licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The colorized images in `docs/` are shared under the same license.

Please only colorize books you have the right to use, and respect the creators whose work you enjoy.
