"""Local web UI: drop a PDF/EPUB/CBZ, tune the look on a sample page, get it back colorized."""
import io
import os
import queue
import shutil
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image
from pydantic import BaseModel, Field

from books import SUPPORTED_SUFFIXES, Options, load_page, page_count, pick_sample_index, process_book

ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
PREVIEW_MAX_SIDE = 900
SAMPLE_MAX_SIDE = 1400
SAMPLE_PAGES_KEPT = 6  # per-draft preview pages held in memory
NO_STORE = {"Cache-Control": "no-store"}


class Settings(BaseModel):
    size: int = Field(576, ge=256, le=1024)
    denoise: int = Field(25, ge=0, le=75)
    skip_colored: bool = True
    saturation: float = Field(1.0, ge=0, le=2.5)
    warmth: float = Field(0.0, ge=-1, le=1)
    hue: float = Field(0.0, ge=-180, le=180)
    clean_whites: float = Field(0.5, ge=0, le=1)

    def to_options(self) -> Options:
        from colorizer import Tone

        return Options(
            size=self.size,
            denoise_sigma=self.denoise,
            skip_colored=self.skip_colored,
            tone=Tone(saturation=self.saturation, warmth=self.warmth, hue=self.hue,
                      clean_whites=self.clean_whites),
        )


@dataclass
class Job:
    id: str
    name: str
    src: Path
    dst: Path
    status: str = "draft"  # draft | queued | running | done | error
    options: Options | None = None
    done: int = 0
    total: int = 0
    error: str = ""
    preview: bytes | None = field(default=None, repr=False)
    pages: int | None = None
    sample_index: int | None = None  # auto-picked preview page
    sample_pages: dict = field(default_factory=dict, repr=False)  # page -> downscaled original
    sample_cache: dict = field(default_factory=dict, repr=False)  # (page, size, denoise) -> small rgb
    sample_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


jobs: dict[str, Job] = {}
job_queue: "queue.Queue[Job]" = queue.Queue()
gpu_lock = threading.Lock()  # the batch worker and live previews share one model
_colorizer = None
_colorizer_lock = threading.Lock()
model_status = {"state": "loading", "device": "", "error": ""}


def _get_colorizer():
    global _colorizer
    with _colorizer_lock:
        if _colorizer is None:
            model_status.update(state="loading", error="")
            try:
                from colorizer import MangaColorizer  # heavy import: torch + weights

                _colorizer = MangaColorizer()
            except Exception as exc:
                model_status.update(state="error", error=str(exc))
                raise
            model_status.update(state="ready", device=_describe_device(_colorizer.device))
    return _colorizer


def _describe_device(device: str) -> str:
    if device == "cuda":
        import torch

        return f"GPU ({torch.cuda.get_device_name(0)})"
    return "CPU (slow: about 10-20 s per page)"


def _reset_data_dir() -> None:
    """Start clean each run; works even when DATA_DIR is a mounted volume."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for child in DATA_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


class _GpuLockedColorizer:
    """Serializes GPU inference so live previews can run while a book is processing."""

    def __init__(self, inner):
        self._inner = inner

    def is_already_colored(self, image):
        return self._inner.is_already_colored(image)

    def colorize(self, *args, **kwargs):
        with gpu_lock:
            return self._inner.colorize(*args, **kwargs)


def _worker() -> None:
    while True:
        job = job_queue.get()
        if job.status != "queued":
            continue
        job.status = "running"

        def progress(done: int, total: int, colored: Image.Image | None) -> None:
            job.done, job.total = done, total
            if colored is not None:
                job.preview = _jpeg(colored, PREVIEW_MAX_SIDE)

        try:
            process_book(job.src, job.dst, _GpuLockedColorizer(_get_colorizer()), job.options, progress)
            job.status = "done"
        except Exception as exc:
            traceback.print_exc()
            job.status, job.error = "error", str(exc)
        finally:
            job.src.unlink(missing_ok=True)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    _reset_data_dir()
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_get_colorizer, daemon=True).start()  # warm up the model
    yield


app = FastAPI(title="Manga Colorizer", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text()


@app.get("/api/health")
def health() -> dict:
    return model_status


@app.post("/api/jobs")
def upload(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "book").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use PDF, EPUB or CBZ.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = DATA_DIR / job_id
    job_dir.mkdir()
    src = job_dir / f"input{suffix}"
    with src.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    out_suffix = ".cbz" if suffix == ".zip" else suffix
    job = Job(id=job_id, name=f"{Path(name).stem} (colored){out_suffix}", src=src,
              dst=job_dir / f"output{out_suffix}")
    jobs[job_id] = job
    return _job_state(job)


@app.post("/api/jobs/{job_id}/start")
def start(job_id: str, settings: Settings) -> dict:
    job = _find(job_id)
    if job.status != "draft":
        raise HTTPException(409, "Already started")
    job.options = settings.to_options()
    job.status = "queued"
    job.sample_pages.clear()
    job.sample_cache.clear()
    job_queue.put(job)
    return _job_state(job)


@app.get("/api/jobs/{job_id}/sample")
def sample_info(job_id: str) -> dict:
    """Page count and the auto-picked preview page (skips covers and blank pages)."""
    job = _find(job_id)
    with job.sample_lock:
        if job.pages is None:
            try:
                job.pages = page_count(job.src)
                job.sample_index = pick_sample_index(job.src, _get_colorizer())
            except Exception as exc:
                raise HTTPException(422, f"Could not read pages: {exc}")
    return {"page": job.sample_index, "pages": job.pages}


@app.get("/api/jobs/{job_id}/sample/original")
def sample_original(job_id: str, page: int = Query(0, ge=0)) -> Response:
    return Response(_jpeg(Image.fromarray(_sample(_find(job_id), page)), SAMPLE_MAX_SIDE),
                    media_type="image/jpeg", headers=NO_STORE)


@app.get("/api/jobs/{job_id}/sample/colored")
def sample_colored(job_id: str, page: int = Query(0, ge=0), settings: Settings = Depends()) -> Response:
    from colorizer import compose

    job = _find(job_id)
    original = _sample(job, page)
    key = (page, settings.size, settings.denoise)
    small = job.sample_cache.get(key)
    if small is None:
        with gpu_lock:
            small = _get_colorizer().infer_small(original, settings.size, settings.denoise)
        job.sample_cache[key] = small
    colored = compose(original, small, settings.to_options().tone)
    return Response(_jpeg(Image.fromarray(colored), SAMPLE_MAX_SIDE), media_type="image/jpeg",
                    headers=NO_STORE)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [_job_state(job) for job in jobs.values()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _job_state(_find(job_id))


@app.get("/api/jobs/{job_id}/preview")
def get_preview(job_id: str) -> Response:
    job = _find(job_id)
    if job.preview is None:
        raise HTTPException(404, "No preview yet")
    return Response(job.preview, media_type="image/jpeg", headers=NO_STORE)


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str) -> FileResponse:
    job = _find(job_id)
    if job.status != "done":
        raise HTTPException(409, "Job not finished")
    return FileResponse(job.dst, filename=job.name)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    job = _find(job_id)
    if job.status == "running":
        raise HTTPException(409, "Job is running")
    job.status = "removed"  # the worker skips it if still queued
    shutil.rmtree(job.dst.parent, ignore_errors=True)
    del jobs[job_id]
    return {"ok": True}


def _sample(job: Job, page: int) -> np.ndarray:
    with job.sample_lock:
        if job.status != "draft":
            raise HTTPException(409, "Samples are only available before starting")
        if page not in job.sample_pages:
            try:
                image = load_page(job.src, page)
            except IndexError:
                raise HTTPException(404, "No such page")
            except Exception as exc:
                raise HTTPException(422, f"Could not read page {page + 1}: {exc}")
            image.thumbnail((SAMPLE_MAX_SIDE, SAMPLE_MAX_SIDE))
            if len(job.sample_pages) >= SAMPLE_PAGES_KEPT:
                oldest = next(iter(job.sample_pages))
                del job.sample_pages[oldest]
                for key in [key for key in job.sample_cache if key[0] == oldest]:
                    del job.sample_cache[key]
            job.sample_pages[page] = np.asarray(image.convert("RGB"))
        return job.sample_pages[page]


def _jpeg(image: Image.Image, max_side: int) -> bytes:
    thumb = image.copy()
    thumb.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    thumb.convert("RGB").save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def _find(job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job


def _job_state(job: Job) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "done": job.done,
        "total": job.total,
        "error": job.error,
        "has_preview": job.preview is not None,
    }
