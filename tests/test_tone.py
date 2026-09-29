import cv2
import numpy as np
import pytest

from colorizer import Tone, compose

RED_OUTPUT = np.full((50, 50, 3), (200, 90, 80), np.uint8)


def chroma(tone: Tone, level: int = 128) -> tuple[float, float]:
    original = np.full((200, 200, 3), level, np.uint8)
    lab = cv2.cvtColor(compose(original, RED_OUTPUT, tone), cv2.COLOR_RGB2LAB).astype(float)
    return lab[..., 1].mean() - 128, lab[..., 2].mean() - 128


def test_keeps_original_lightness():
    original = np.full((200, 200, 3), 128, np.uint8)
    lab = cv2.cvtColor(compose(original, RED_OUTPUT, Tone()), cv2.COLOR_RGB2LAB)
    expected = cv2.cvtColor(original, cv2.COLOR_RGB2LAB)[..., 0]
    assert np.abs(lab[..., 0].astype(int) - expected).max() <= 2


def test_zero_saturation_is_grayscale():
    a, b = chroma(Tone(saturation=0))
    assert abs(a) < 1 and abs(b) < 1


def test_saturation_scales_chroma():
    a1, b1 = chroma(Tone(clean_whites=0))
    a2, b2 = chroma(Tone(saturation=2, clean_whites=0))
    assert np.hypot(a2, b2) == pytest.approx(2 * np.hypot(a1, b1), rel=0.1)


def test_warmth_moves_toward_yellow_and_cool_toward_blue():
    _, neutral = chroma(Tone(clean_whites=0))
    _, warm = chroma(Tone(warmth=1, clean_whites=0))
    _, cool = chroma(Tone(warmth=-1, clean_whites=0))
    assert warm > neutral > cool


def test_hue_rotation_preserves_chroma_magnitude():
    a1, b1 = chroma(Tone(clean_whites=0))
    a2, b2 = chroma(Tone(hue=90, clean_whites=0))
    assert np.hypot(a2, b2) == pytest.approx(np.hypot(a1, b1), rel=0.1)
    assert (a1, b1) != pytest.approx((a2, b2), abs=5)


def test_clean_whites_strips_tint_from_paper_only():
    a, b = chroma(Tone(clean_whites=1), level=250)
    assert abs(a) < 1 and abs(b) < 1
    a_mid, b_mid = chroma(Tone(clean_whites=1), level=128)
    assert np.hypot(a_mid, b_mid) > 30
