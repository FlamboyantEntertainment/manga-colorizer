"""Writes a local test page for the browser extension.

Synthetic grayscale "manga" pages, plus the cases the extension must handle: lazy loading
through data-src, native loading="lazy", <picture>, a blob: URL, a blob: URL the site revokes
after load, a reader that scrolls inside a div, a small icon (ignored) and a color image (left as is).

    .venv/bin/python tests/extension-page/make_page.py
    .venv/bin/python -m http.server 8765 -d tests/extension-page
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).parent
PAGE_SIZE = (800, 1200)
PAGE_COUNT = 11
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
<h2>Scroll container: the second page sits below the visible area and should still color early</h2>
<div id="scroller" style="height:600px;overflow:auto;max-width:800px;margin:0 auto">
  <img src="page-8.png" alt=""><img src="page-9.png" alt=""><img src="page-10.png" alt="">
</div>
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
  <h2>9. blob: URL revoked after load</h2><img id="revoked" alt="" width="800" height="1200">
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
  // Reader that unscrambles pages into blob: URLs and frees them once shown.
  fetch("page-11.png").then((response) => response.blob()).then((blob) => {{
    const img = document.getElementById("revoked");
    img.onload = () => URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
  }});
</script>
</body>
</html>
""")


if __name__ == "__main__":
    main()
