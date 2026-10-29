"""Scan-Pipeline: Eckmarken finden → entzerren → Bubbles lesen → QR dekodieren.

Voll deterministisch (kein ML), CPU-only. Alle Geometrie kommt aus
app.formgen.layout.compute_layout – Generator und Reader teilen dieselbe Geometrie.
Bubbles werden nur auf Seite 1 gelesen; Seite 2 (Freitext) wird nur entzerrt + QR.
"""
from __future__ import annotations

import dataclasses
from itertools import combinations

import cv2
import numpy as np

from ..formgen.layout import (
    CANONICAL_DPI,
    MARK_SIZE_MM,
    PAGE_W_MM,
    canonical_size_px,
    compute_layout,
    mark_centers_px,
    mm_to_px,
)
from ..formgen.spec import NO_ANSWER
from ..formgen.stack_id import parse_payload

# Ink-Dichten (Anteil dunkler Pixel im inneren Bubble-Disk)
INK_FILLED = 0.40
INK_EMPTY = 0.15
REVIEW_CONF = 0.55

MIN_QUAD_SCORE = 0.82


@dataclasses.dataclass
class AnswerReading:
    question_id: str
    value: str | None
    confidence: float
    needs_review: bool
    note: str = ""


@dataclasses.dataclass
class ReadResult:
    stack_id: str | None
    quad_score: float
    answers: list[AnswerReading]
    warnings: list[str]
    page: int = 1           # 1 = Fragen, 2 = Freitext (aus dem QR; unlesbar → 1)
    spec: object = None     # die beim Lesen tatsächlich verwendete Spec (Seite 1)

    @property
    def needs_review(self) -> bool:
        return any(a.needs_review for a in self.answers) or self.stack_id is None


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Sortiert 4 Punkte zu TL, TR, BR, BL."""
    s = pts.sum(axis=1)
    d = (pts[:, 0] - pts[:, 1]).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    # x-y ist für TR maximal (rechtes oberes Ecke) und für BL minimal
    tr = pts[np.argmax(d)]
    bl = pts[np.argmin(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _mark_candidates(bw: np.ndarray) -> list[tuple[float, np.ndarray]]:
    """Gefüllte, quadratische schwarze Blöcke in Markierungsgröße finden."""
    h_img, w_img = bw.shape
    s_exp = w_img * (MARK_SIZE_MM / PAGE_W_MM)
    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cands: list[tuple[float, np.ndarray]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if not (0.55 * s_exp <= w <= 1.9 * s_exp and 0.55 * s_exp <= h <= 1.9 * s_exp):
            continue
        if not (0.7 <= w / max(h, 1) <= 1.43):
            continue
        if cv2.contourArea(cnt) / float(w * h) < 0.6:
            continue
        fill = float(bw[y:y + h, x:x + w].mean()) / 255.0
        if fill < 0.55:
            continue
        # Score: perfekte Quadrate mit voller Füllung gewinnen
        score = fill * (1.0 - abs(w - h) / max(w, h))
        cands.append((score, np.array([x + w / 2.0, y + h / 2.0], dtype=np.float32)))
    cands.sort(key=lambda t: -t[0])
    # großzügig: dunkle Quadrate im Logo dürfen die echten Marken nicht verdrängen
    return cands[:20]


def _select_quad(cands: list[tuple[float, np.ndarray]], shape: tuple[int, int]) -> tuple[np.ndarray, float] | None:
    """Wählt das 4er-Set, das am besten ein aufgespanntes Rechteck bildet.

    Bewusst UNABHÄNGIG von der Bildgröße (Rand/Rasiermesser-Crop egal):
    Kriterien sind gleiche Gegenseiten + rechte Winkel + Mindestspanne.
    """
    if len(cands) < 4:
        return None
    h_img, w_img = shape
    pts = np.array([c[1] for c in cands], dtype=np.float32)
    best_q, best_score = None, -1.0
    for combo in combinations(range(len(pts)), 4):
        q = _order_quad(pts[list(combo)])
        d = lambda a, b: float(np.hypot(*(q[a] - q[b])))  # noqa: E731
        top, bottom, left, right = d(0, 1), d(3, 2), d(0, 3), d(1, 2)
        if min(top, bottom, left, right) < 1.0:
            continue  # degeneriert (doppelte/identische Punkte)
        s1 = min(top, bottom) / max(top, bottom)
        s2 = min(left, right) / max(left, right)
        e1 = (q[1] - q[0]) / max(top, 1e-6)
        e2 = (q[3] - q[0]) / max(left, 1e-6)
        orth = 1.0 - abs(float(np.dot(e1, e2)))
        score = s1 * s2 * orth
        if score > best_score:
            best_score, best_q = score, q
    if best_score < MIN_QUAD_SCORE:
        return None
    # Span-Check: Marker müssen die Seite dominieren (verwirft QR-Finder & Textklumpen)
    span_x = float(best_q[:, 0].max() - best_q[:, 0].min())
    span_y = float(best_q[:, 1].max() - best_q[:, 1].min())
    if span_x < 0.45 * w_img or span_y < 0.45 * h_img:
        return None
    return best_q, best_score


def _warp(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    dst = np.array(mark_centers_px(CANONICAL_DPI), dtype=np.float32)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, canonical_size_px(CANONICAL_DPI), flags=cv2.INTER_CUBIC)


def _decode_qr(warped: np.ndarray, original: np.ndarray) -> str | None:
    det = cv2.QRCodeDetector()
    for img in (warped, original, cv2.resize(warped, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)):
        try:
            text, _, _ = det.detectAndDecode(img)
        except cv2.error:
            text = ""
        if text and parse_payload(text):
            return text
    return None


def _disk_ink(warped_gray: np.ndarray, cx: int, cy: int, r_in: int, r_bg: int) -> float:
    """Anteil dunkler Pixel im Inneren, Schwellwert adaptiv aus umgebendem Ring."""
    h, w = warped_gray.shape
    if cy - r_bg < 0 or cy + r_bg >= h or cx - r_bg < 0 or cx + r_bg >= w:
        return 0.0
    ring = np.concatenate([
        warped_gray[cy - r_bg:cy - r_in + 1, cx - r_bg:cx + r_bg].ravel(),
        warped_gray[cy + r_in - 1:cy + r_bg, cx - r_bg:cx + r_bg].ravel(),
        warped_gray[cy - r_bg:cy + r_bg, cx - r_bg:cx - r_in + 1].ravel(),
        warped_gray[cy - r_bg:cy + r_bg, cx + r_in - 1:cx + r_bg].ravel(),
    ])
    bg = float(np.median(ring))
    t = bg * 0.78
    disk = warped_gray[cy - r_in:cy + r_in, cx - r_in:cx + r_in]
    return float((disk < t).mean())


def _read_bubbles(warped_gray: np.ndarray, spec) -> list[AnswerReading]:
    lay = compute_layout(spec)
    r = mm_to_px(lay.bubble_r)
    r_in, r_bg = int(round(r * 0.60)), int(round(r * 1.45))
    readings: list[AnswerReading] = []
    for q, row in zip(spec.questions, lay.rows):
        labels = q.answers()   # echte Optionen + "Keine Angabe" (letzte Spalte)
        inks = []
        for (xmm, ymm) in row.bubbles:
            cx, cy = int(round(mm_to_px(xmm))), int(round(mm_to_px(ymm)))
            inks.append(_disk_ink(warped_gray, cx, cy, r_in, r_bg))
        filled = [j for j, v in enumerate(inks) if v >= INK_FILLED]
        amb = [j for j, v in enumerate(inks) if INK_EMPTY < v < INK_FILLED]
        if len(filled) == 1 and not amb:
            j = filled[0]
            if labels[j] == NO_ANSWER:
                readings.append(AnswerReading(q.id, NO_ANSWER, 1.0, False,
                                              "explizit keine Angabe"))
                continue
            conf = min(1.0, (inks[j] - INK_EMPTY) / (0.75 - INK_EMPTY))
            readings.append(AnswerReading(q.id, labels[j], round(conf, 3),
                                          conf < REVIEW_CONF, ""))
        elif len(filled) >= 2:
            readings.append(AnswerReading(q.id, None, 0.0, True,
                                          "mehrere Markierungen"))
        elif amb:
            readings.append(AnswerReading(q.id, None, 0.0, True,
                                          "Markierung unklar"))
        else:
            readings.append(AnswerReading(q.id, None, 1.0, False, "keine Antwort"))
    return readings


def read_scan(image_bytes: bytes, spec) -> ReadResult:
    """Hauptfunktion: Scan-Bild (JPEG/PNG) → strukturierte, bewertete Antworten.

    spec: SurveySpec ODER Funktion stack_id (None = unbekannt) → SurveySpec;
    so wird erst der Stapel aus dem QR bestimmt und dann mit dessen Spec gelesen."""
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    original = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if original is None:
        raise ValueError("Bild konnte nicht dekodiert werden")
    gray = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    warnings: list[str] = []
    cands = _mark_candidates(bw)
    sel = _select_quad(cands, gray.shape)
    if sel is None:
        raise ValueError("Eckmarken nicht gefunden – ist der Bogen vollständig sichtbar?")
    quad, score = sel
    warped = _warp(original, quad)
    warped_gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

    stack_id, page = None, 1
    payload = _decode_qr(warped, original)
    if payload:
        parsed = parse_payload(payload)
        if parsed:
            stack_id, page = parsed["s"], parsed["p"]
    else:
        # ponytail: unlesbarer QR gilt als Seite 1 – landet ohnehin im Review
        warnings.append("QR-Code nicht lesbar – Stapel muss manuell zugeordnet werden")

    used, answers = None, []
    if page != 2:
        used = spec(stack_id) if callable(spec) else spec  # genau einmal auflösen
        answers = _read_bubbles(warped_gray, used)
    return ReadResult(stack_id=stack_id, quad_score=round(score, 4),
                      answers=answers, warnings=warnings, page=page, spec=used)
