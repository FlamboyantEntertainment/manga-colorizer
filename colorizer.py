"""manga-colorization-v2 wrapper that keeps the page at full resolution.

The network only works well around 576 px, so we colorize a downscaled copy
and transfer just the chroma (LAB a/b channels) back onto the original
full-resolution luminance. Linework and screentone stay as crisp as the source.
Tone adjustments are applied to that chroma, so they cost no extra inference.
"""
import os
from dataclasses import dataclass

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision.transforms import ToTensor

from mc2.denoising.denoiser import FFDNetDenoiser
from mc2.networks.models import Colorizer
from mc2.utils.utils import resize_pad
from weights import ensure_weights

# Mean HSV saturation above which a page is treated as already colored.
COLOR_SATURATION_THRESHOLD = 28
# OpenCV 8-bit LAB: a/b are offset by 128; L is 0..255.
LAB_CHROMA_OFFSET = 128.0
WARMTH_MAX_SHIFT = 18.0
CLEAN_WHITES_START = 200.0
CLEAN_WHITES_END = 245.0


@dataclass
class Tone:
    saturation: float = 1.0   # chroma multiplier, 0 = grayscale
    warmth: float = 0.0       # -1 cool .. +1 warm
    hue: float = 0.0          # degrees of rotation in the a/b plane
    clean_whites: float = 0.5  # 0..1, how much tint to strip from paper/speech bubbles


class MangaColorizer:
    def __init__(self, device: str | None = None):
        self.device = device or os.environ.get("DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
        models_dir = ensure_weights()
        self._net = Colorizer().to(self.device)
        self._net.generator.load_state_dict(
            torch.load(models_dir / "generator.zip", map_location=self.device)
        )
        self._net.eval()
        self._denoiser = FFDNetDenoiser(self.device, _weights_dir=str(models_dir))
        self._to_tensor = ToTensor()

    @staticmethod
    def is_already_colored(image: Image.Image) -> bool:
        small = image.convert("RGB").resize((256, 256))
        hsv = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2HSV)
        return float(hsv[..., 1].mean()) > COLOR_SATURATION_THRESHOLD

    def colorize(self, image: Image.Image, size: int = 576, denoise_sigma: int = 25,
                 tone: Tone | None = None) -> Image.Image:
        original = np.asarray(image.convert("RGB"))
        small_rgb = self.infer_small(original, size, denoise_sigma)
        return Image.fromarray(compose(original, small_rgb, tone or Tone()))

    @torch.inference_mode()
    def infer_small(self, original: np.ndarray, size: int = 576, denoise_sigma: int = 25) -> np.ndarray:
        """Run the network; returns a low-res RGB colorization of the page."""
        height, width = original.shape[:2]
        size = max(256, size - size % 32)
        size = min(size, min(height, width) - min(height, width) % 32)

        work = original
        if denoise_sigma > 0:
            work = self._denoiser.get_denoised_image(work, sigma=denoise_sigma)

        work, pad = resize_pad(work, size)
        tensor = self._to_tensor(work).unsqueeze(0).to(self.device)
        hint = torch.zeros(1, 4, tensor.shape[2], tensor.shape[3], device=self.device)

        fake_color, _ = self._net(torch.cat([tensor, hint], 1))
        result = fake_color[0].permute(1, 2, 0).float().cpu().numpy() * 0.5 + 0.5
        if pad[0]:
            result = result[: -pad[0]]
        if pad[1]:
            result = result[:, : -pad[1]]
        return (np.clip(result, 0, 1) * 255).astype(np.uint8)


def compose(original_rgb: np.ndarray, small_rgb: np.ndarray, tone: Tone) -> np.ndarray:
    """Original luminance + upscaled, tone-adjusted chroma from the network output."""
    height, width = original_rgb.shape[:2]
    colored_lab = cv2.resize(cv2.cvtColor(small_rgb, cv2.COLOR_RGB2LAB), (width, height),
                             interpolation=cv2.INTER_CUBIC)
    original_lab = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2LAB)
    lightness = original_lab[..., 0]

    chroma_a = colored_lab[..., 1].astype(np.float32) - LAB_CHROMA_OFFSET
    chroma_b = colored_lab[..., 2].astype(np.float32) - LAB_CHROMA_OFFSET

    if tone.hue:
        angle = np.deg2rad(tone.hue)
        cos_angle, sin_angle = np.cos(angle), np.sin(angle)
        chroma_a, chroma_b = (chroma_a * cos_angle - chroma_b * sin_angle,
                              chroma_a * sin_angle + chroma_b * cos_angle)

    # Fade chroma out on near-white areas (paper, speech bubbles).
    white_fade = np.float32(1.0)
    if tone.clean_whites > 0:
        ramp = np.clip((lightness.astype(np.float32) - CLEAN_WHITES_START)
                       / (CLEAN_WHITES_END - CLEAN_WHITES_START), 0, 1)
        white_fade = 1 - tone.clean_whites * ramp

    if tone.warmth:
        # Warm = toward yellow (+b) with a touch of red (+a); cool is the reverse.
        shift = tone.warmth * WARMTH_MAX_SHIFT
        chroma_b += shift
        chroma_a += shift * 0.3

    scale = tone.saturation * white_fade
    chroma_a *= scale
    chroma_b *= scale

    merged = np.dstack([
        lightness,
        np.clip(chroma_a + LAB_CHROMA_OFFSET, 0, 255).astype(np.uint8),
        np.clip(chroma_b + LAB_CHROMA_OFFSET, 0, 255).astype(np.uint8),
    ])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
