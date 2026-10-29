"""Synthetisch ausgefüllte Bögen für Tests: PDF rendern, ankreuzeln, verunstalten.

Simuliert einen realistischen Scan: Rotation/Skew, JPEG-Kompression, Rauschen,
leicht unsaubere Kugelschreiber-Kreuze.
"""
from __future__ import annotations

import io

import cv2
import numpy as np

from ..formgen.layout import FREE_BOX_MM, compute_layout, mm_to_px


def render_pdf_to_image(pdf_bytes: bytes, dpi: int = 150, page: int = 0) -> np.ndarray:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    page = doc[page]
    pil = page.render(scale=dpi / 72.0).to_pil()
    return cv2.cvtColor(np.array(pil.convert("RGB")), cv2.COLOR_RGB2BGR)


def fill_form(img: np.ndarray, spec, answers: dict[str, int | None],
              rng: np.random.Generator | None = None, dpi: int = 150) -> np.ndarray:
    """Zeichnet Kreuze in die Bubbles. answers: question_id → Optionsindex (oder None)."""
    rng = rng or np.random.default_rng()
    out = img.copy()
    for q, row in zip(spec.questions, compute_layout(spec).rows):
        j = answers.get(q.id)
        if j is None:
            continue
        xmm, ymm = row.bubbles[j]
        cx, cy = int(round(mm_to_px(xmm, dpi))), int(round(mm_to_px(ymm, dpi)))
        r = mm_to_px(2.2, dpi)
        # leicht versetzt + Radius-Jitter = menschliches Ankrezzen
        cx += int(rng.normal(0, 1.2))
        cy += int(rng.normal(0, 1.2))
        rr = r * rng.uniform(0.85, 1.1)
        color = (60, 45, 25)  # dunkles Kugelschreiber-Blau-Schwarz (BGR)
        cv2.circle(out, (cx, cy), int(round(rr)), color, thickness=-1, lineType=cv2.LINE_AA)
    return out


def scribble(img: np.ndarray, rng: np.random.Generator | None = None,
             dpi: int = 150, lines: int = 4) -> np.ndarray:
    """Etwas „Handschrift“-Gekritzel in den Freitextkasten (Seite 2)."""
    rng = rng or np.random.default_rng()
    out = img.copy()
    x0, y0, x1, _ = FREE_BOX_MM
    for k in range(lines):
        y = y0 + 8.0 + k * 10.0
        xs = np.linspace(x0 + 6, x0 + 6 + rng.uniform(60, x1 - x0 - 15), 60)
        ys = y + 1.6 * np.sin(xs * rng.uniform(1.5, 3.0)) + rng.normal(0, 0.3, xs.size)
        pts = np.array([[mm_to_px(a, dpi), mm_to_px(b, dpi)] for a, b in zip(xs, ys)], np.int32)
        cv2.polylines(out, [pts], False, (60, 45, 25), 2, cv2.LINE_AA)
    return out


def simulate_scan(img: np.ndarray, rng: np.random.Generator | None = None,
                  jpeg_quality: int = 78, border_px: int = 70) -> bytes:
    """Wie ein Flachbettscanner: ganzer Bogen sichtbar auf weißem Hintergrund,
    dazu Rotation, leichtes Weichzeichnen, Rauschen, JPEG."""
    rng = rng or np.random.default_rng()
    h, w = img.shape[:2]
    padded = cv2.copyMakeBorder(img, border_px, border_px, border_px, border_px,
                                cv2.BORDER_CONSTANT, value=(255, 255, 255))
    angle = rng.uniform(-2.5, 2.5)
    M = cv2.getRotationMatrix2D((padded.shape[1] / 2, padded.shape[0] / 2), angle, 1.0)
    rotated = cv2.warpAffine(padded, M, (padded.shape[1], padded.shape[0]),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    rotated = cv2.GaussianBlur(rotated, (3, 3), 0.6)
    noise = rng.normal(0, 4.0, rotated.shape).astype(np.float32)
    noisy = np.clip(rotated.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    if not ok:
        raise RuntimeError("JPEG-Encoding fehlgeschlagen")
    return enc.tobytes()
