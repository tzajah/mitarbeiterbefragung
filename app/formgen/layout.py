"""Feste geometrische Formular-Geometrie (Millimeter auf A4).

DIESE Datei ist die single source of truth: Der Generator (PDF) und der
Scanner-Reader (OpenCV) rechnen beide mit exakt diesen Konstanten.
"""
from __future__ import annotations

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

# Eckmarken (gefüllte schwarze Quadrate), Zentren: TL, TR, BR, BL
MARK_SIZE_MM = 5.0
MARK_CENTERS_MM = [
    (12.0, 12.0),
    (198.0, 12.0),
    (198.0, 290.0),
    (12.0, 290.0),
]

# Fragenraster
ROW_Y0_MM = 46.0        # y-Zentrum der ersten Fragezeile
ROW_PITCH_MM = 7.8      # Zeilenabstand
MAX_QUESTIONS = 30

# Antwort-Bubbles
BUBBLE_X0_MM = 122.0    # x-Zentrum der ersten Bubble-Spalte
BUBBLE_PITCH_MM = 15.5
BUBBLE_R_MM = 2.6
MAX_OPTIONS = 5

# QR-Code (Stapel-/Abteilungs-ID), oben rechts
QR_BOX_MM = (170.0, 10.0, 190.0, 30.0)

# Freitextfeld unten
TEXT_BOX_MM = (15.0, 274.0, 195.0, 283.0)

# Kanonische Auflösung nach Perspektivkorrektur
CANONICAL_DPI = 200


def mm_to_px(mm: float, dpi: int = CANONICAL_DPI) -> float:
    return mm * dpi / 25.4


def canonical_size_px(dpi: int = CANONICAL_DPI) -> tuple[int, int]:
    return (round(mm_to_px(PAGE_W_MM, dpi)), round(mm_to_px(PAGE_H_MM, dpi)))


def mark_centers_px(dpi: int = CANONICAL_DPI) -> list[tuple[float, float]]:
    return [(mm_to_px(x, dpi), mm_to_px(y, dpi)) for x, y in MARK_CENTERS_MM]


def bubble_centers_mm(row_index: int, n_options: int) -> list[tuple[float, float]]:
    y = ROW_Y0_MM + row_index * ROW_PITCH_MM
    return [
        (BUBBLE_X0_MM + j * BUBBLE_PITCH_MM, y)
        for j in range(min(n_options, MAX_OPTIONS))
    ]
