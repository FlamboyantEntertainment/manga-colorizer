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


def test_model_load_failure_returns_500_not_503(client, fake, monkeypatch):
    # 503 means "try again soon" to the extension; a failed model load won't fix itself.
    monkeypatch.setitem(server.model_status, "state", "error")
    monkeypatch.setitem(server.model_status, "error", "CUDA out of memory")
    response = post(client, image_bytes())
    assert response.status_code == 500
    assert "CUDA out of memory" in response.json()["detail"]
