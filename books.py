"""Unpack PDF / EPUB / CBZ, colorize every page image, repack to the same format."""
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pymupdf
from PIL import Image

from colorizer import MangaColorizer, Tone

SUPPORTED_SUFFIXES = {".pdf", ".epub", ".cbz", ".zip"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MIN_PAGE_SIDE = 400  # smaller images are icons/ornaments, leave them alone
PDF_RENDER_DPI = 200
JPEG_QUALITY = 92
SAMPLE_SKIP_PAGES = 4
BLANK_PAGE_STD = 25.0


@dataclass
class Options:
    size: int = 576
    denoise_sigma: int = 25
    skip_colored: bool = True
    tone: Tone = field(default_factory=Tone)


# (pages_done, pages_total, last_colored_page_or_None)
ProgressFn = Callable[[int, int, Image.Image | None], None]


def process_book(src: Path, dst: Path, colorizer: MangaColorizer, options: Options, progress: ProgressFn) -> None:
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        _process_pdf(src, dst, colorizer, options, progress)
    elif suffix in SUPPORTED_SUFFIXES:
        _process_zip(src, dst, colorizer, options, progress, is_epub=suffix == ".epub")
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


def _colorize_page(image: Image.Image, colorizer: MangaColorizer, options: Options) -> Image.Image | None:
    """Returns the colored page, or None when the page should be kept as-is."""
    if min(image.size) < MIN_PAGE_SIDE:
        return None
    if options.skip_colored and colorizer.is_already_colored(image):
        return None
    return colorizer.colorize(image, size=options.size, denoise_sigma=options.denoise_sigma, tone=options.tone)


def _encode(image: Image.Image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    if fmt == "PNG":
        image.save(buffer, "PNG", optimize=True)
    elif fmt == "WEBP":
        image.save(buffer, "WEBP", quality=90)
    else:
        image.convert("RGB").save(buffer, "JPEG", quality=JPEG_QUALITY, subsampling=0)
    return buffer.getvalue()


def _pdf_page_image(doc: pymupdf.Document, page: pymupdf.Page) -> Image.Image:
    """Prefer the embedded scan at native resolution; otherwise render the page."""
    images = page.get_images(full=True)
    if len(images) == 1 and page.rotation == 0:
        try:
            extracted = doc.extract_image(images[0][0])
            image = Image.open(io.BytesIO(extracted["image"]))
            image.load()
            if min(image.size) >= MIN_PAGE_SIDE:
                return image.convert("RGB")
        except Exception:
            pass
    pixmap = page.get_pixmap(dpi=PDF_RENDER_DPI, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _process_pdf(src: Path, dst: Path, colorizer: MangaColorizer, options: Options, progress: ProgressFn) -> None:
    with pymupdf.open(src) as doc, pymupdf.open() as out:
        total = doc.page_count
        progress(0, total, None)
        for page_index in range(total):
            page = doc[page_index]
            image = _pdf_page_image(doc, page)
            colored = _colorize_page(image, colorizer, options)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=_encode(colored or image, "JPEG"))
            progress(page_index + 1, total, colored)
        out.save(dst, garbage=3, deflate=True)


def _process_zip(src: Path, dst: Path, colorizer: MangaColorizer, options: Options,
                 progress: ProgressFn, is_epub: bool) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        entries = zin.infolist()
        if is_epub:
            # EPUB spec: "mimetype" must be the first entry and stored uncompressed.
            entries.sort(key=lambda entry: entry.filename != "mimetype")
        image_entries = [entry for entry in entries if Path(entry.filename).suffix.lower() in IMAGE_SUFFIXES]
        image_names = {entry.filename for entry in image_entries}
        total = len(image_entries)
        done = 0
        progress(0, total, None)

        for entry in entries:
            data = zin.read(entry.filename)
            if entry.filename == "mimetype":
                zout.writestr(entry, data, compress_type=zipfile.ZIP_STORED)
                continue
            if entry.filename not in image_names:
                zout.writestr(entry, data)
                continue

            colored = None
            try:
                image = Image.open(io.BytesIO(data))
                fmt = image.format or "JPEG"
                colored = _colorize_page(image, colorizer, options)
                if colored is not None:
                    data = _encode(colored, fmt)
            except Exception:
                pass  # unreadable image: keep the original bytes
            zout.writestr(entry, data, compress_type=zipfile.ZIP_STORED)  # images don't deflate
            done += 1
            progress(done, total, colored)


def page_count(src: Path) -> int:
    if src.suffix.lower() == ".pdf":
        with pymupdf.open(src) as doc:
            return doc.page_count
    with zipfile.ZipFile(src) as zin:
        return len(_zip_page_names(zin))


def load_page(src: Path, index: int) -> Image.Image:
    if src.suffix.lower() == ".pdf":
        with pymupdf.open(src) as doc:
            return _pdf_page_image(doc, doc[index])
    with zipfile.ZipFile(src) as zin:
        image = Image.open(io.BytesIO(zin.read(_zip_page_names(zin)[index])))
        image.load()
        return image.convert("RGB")


def pick_sample_index(src: Path, colorizer: MangaColorizer, limit: int = 40) -> int:
    """Index of a representative black-and-white page (skips covers, blanks and front matter)."""
    first_bw = None
    for index in range(min(page_count(src), limit)):
        try:
            image = load_page(src, index)
        except Exception:
            continue
        if min(image.size) < MIN_PAGE_SIDE or colorizer.is_already_colored(image) or _is_blank(image):
            continue
        if index >= SAMPLE_SKIP_PAGES:
            return index
        if first_bw is None:
            first_bw = index
    return first_bw if first_bw is not None else 0


def _is_blank(image: Image.Image) -> bool:
    """Nearly uniform pages (blank or title-only) make poor previews."""
    gray = np.asarray(image.convert("L").resize((256, 256)), dtype=np.float32)
    return float(gray.std()) < BLANK_PAGE_STD


def _zip_page_names(zin: zipfile.ZipFile) -> list[str]:
    return sorted(name for name in zin.namelist() if Path(name).suffix.lower() in IMAGE_SUFFIXES)
