"""Pipeline tests with a fake colorizer: no GPU and no model weights needed."""
import io
import zipfile

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from books import Options, load_page, page_count, pick_sample_index, process_book
from colorizer import MangaColorizer

PAGE_SIZE = (1200, 1800)


class FakeColorizer:
    is_already_colored = staticmethod(MangaColorizer.is_already_colored)

    def colorize(self, image, size=576, denoise_sigma=25, tone=None):
        tinted = np.asarray(image.convert("RGB")).astype(np.int16)
        tinted[..., 2] = np.clip(tinted[..., 2] - 120, 0, 255)  # push toward yellow
        return Image.fromarray(tinted.astype(np.uint8))


def manga_page(seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", PAGE_SIZE, (150, 150, 150))
    draw = ImageDraw.Draw(image)
    for box in [(40, 40, 1160, 700), (40, 740, 580, 1760), (620, 740, 1160, 1760)]:
        draw.rectangle(box, outline="black", fill="white", width=8)
    for _ in range(800):
        x, y = rng.integers(80, 540), rng.integers(780, 1720)
        draw.ellipse((x, y, x + 6, y + 6), fill="black")
    return image


def blank_page() -> Image.Image:
    return Image.new("RGB", PAGE_SIZE, "white")


def title_page() -> Image.Image:
    image = blank_page()
    ImageDraw.Draw(image).text((250, 700), "TITLE", fill="black", font=ImageFont.load_default(size=90))
    return image


def encode(image: Image.Image, fmt: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def saturation(image: Image.Image) -> float:
    return float(np.asarray(image.convert("HSV"))[..., 1].mean())


@pytest.fixture
def cbz(tmp_path):
    path = tmp_path / "book.cbz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("000_cover.jpg", encode(Image.new("RGB", PAGE_SIZE, (200, 40, 60))))
        archive.writestr("001_blank.jpg", encode(blank_page()))
        archive.writestr("002_title.jpg", encode(title_page()))
        for index in range(3, 8):
            archive.writestr(f"{index:03}.jpg", encode(manga_page(index)))
    return path


def test_cbz_roundtrip_colors_pages_and_keeps_colored_cover(cbz, tmp_path):
    out = tmp_path / "out.cbz"
    events = []
    process_book(cbz, out, FakeColorizer(), Options(), lambda done, total, img: events.append((done, total)))

    assert events[-1] == (8, 8)
    with zipfile.ZipFile(cbz) as original, zipfile.ZipFile(out) as result:
        assert result.namelist() == original.namelist()
        cover_before = original.read("000_cover.jpg")
        assert result.read("000_cover.jpg") == cover_before  # already colored: untouched
        page = Image.open(io.BytesIO(result.read("003.jpg")))
        assert page.size == PAGE_SIZE
        assert saturation(page) > 20


def test_epub_keeps_mimetype_first_and_uncompressed(tmp_path):
    src = tmp_path / "book.epub"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("OEBPS/Images/p1.png", encode(manga_page(), "PNG"))
        archive.writestr("OEBPS/Images/icon.png", encode(Image.new("RGB", (64, 64)), "PNG"))
    out = tmp_path / "out.epub"
    process_book(src, out, FakeColorizer(), Options(), lambda *_: None)

    with zipfile.ZipFile(out) as result:
        first = result.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert saturation(Image.open(io.BytesIO(result.read("OEBPS/Images/p1.png")))) > 20
        icon = Image.open(io.BytesIO(result.read("OEBPS/Images/icon.png")))
        assert icon.size == (64, 64)


def test_pdf_roundtrip_keeps_page_count_and_size(tmp_path):
    src = tmp_path / "book.pdf"
    with pymupdf.open() as doc:
        for index in range(3):
            page = doc.new_page(width=600, height=900)
            page.insert_image(page.rect, stream=encode(manga_page(index)))
        doc.save(src)
    out = tmp_path / "out.pdf"
    process_book(src, out, FakeColorizer(), Options(), lambda *_: None)

    with pymupdf.open(out) as doc:
        assert doc.page_count == 3
        assert doc[0].rect.width == 600 and doc[0].rect.height == 900
    assert saturation(load_page(out, 0)) > 20


def test_sample_pick_skips_cover_blank_and_title(cbz):
    assert page_count(cbz) == 8
    index = pick_sample_index(cbz, FakeColorizer())
    assert index >= 3


def test_unsupported_format_raises(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("hi")
    with pytest.raises(ValueError):
        process_book(src, tmp_path / "out.txt", FakeColorizer(), Options(), lambda *_: None)
