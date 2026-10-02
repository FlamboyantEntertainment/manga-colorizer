# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-02

### Added

- Browser extension (Brave/Chrome, load unpacked from `extension/`) that colorizes manga pages in place while you read on a website. It turns on per site, colors pages shortly before they scroll into view, and can flip back to the originals.
- Extension popup with the same controls as the web UI: presets, color strength, warmth, hue shift, clean whites, detail, denoise and keep already-colored pages.
- `POST /api/colorize` endpoint: send one page image, get the colored JPEG back (or 204 when the page is skipped).
- CodeQL code scanning, Dependabot alerts and Dependabot security updates on GitHub.

### Changed

- PyTorch 2.11 → 2.13 and torchvision 0.26 → 0.28. Colorized output is identical and speed is unchanged.
- The GPU image now uses the CUDA 13.0 build instead of CUDA 12.8. It needs NVIDIA driver 580 or newer, and still supports RTX 20-series and newer cards, including RTX 50-series.
- CI runs the extension's JavaScript tests and gives the workflow token read-only access.

### Fixed

- CI installs `httpx2`, which the endpoint test needs.

### Security

- Requires `setuptools` 83 or newer (PYSEC-2026-3447). The upgrade also clears the PyTorch advisory PYSEC-2025-194.

## [0.1.0] - 2026-09-29

### Added

- Self-hosted web app that colorizes whole manga books (PDF, EPUB, CBZ) and returns the same format.
- Live before/after preview on a page of the book, with presets and tone controls.
- Skips already-colored pages and small images.
- Docker images for NVIDIA GPUs (`:latest`) and CPU (`:cpu`).

[Unreleased]: https://github.com/FlamboyantEntertainment/manga-colorizer/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/FlamboyantEntertainment/manga-colorizer/compare/b1...v0.2.0
[0.1.0]: https://github.com/FlamboyantEntertainment/manga-colorizer/releases/tag/b1
