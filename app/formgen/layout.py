"""Formular-Geometrie (Millimeter auf A4, y von OBEN).

DIESE Datei ist die single source of truth: Generator (PDF), Scanner-Reader
(OpenCV) und synth.fill_form rechnen ausschließlich mit compute_layout(spec).
Fix (Geometrie-Referenz der Erkennung): Eckmarken, QR-Box, kanonische DPI.
Variabel (aus der Spec berechnet): Tabellenköpfe, Zeilen, Bubble-Zentren.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from reportlab.pdfbase.pdfmetrics import stringWidth

PAGE_W_MM = 210.0
PAGE_H_MM = 297.0

# Eckmarken (gefüllte schwarze Quadrate), Zentren: TL, TR, BR, BL – auf BEIDEN Seiten
MARK_SIZE_MM = 5.0
MARK_CENTERS_MM = [
    (12.0, 12.0),
    (198.0, 12.0),
    (198.0, 290.0),
    (12.0, 290.0),
]

MAX_QUESTIONS = 30
MAX_OPTIONS = 5         # echte Antwortoptionen je Frage (Extra-Spalte nicht mitgezählt)
MAX_BUBBLES = MAX_OPTIONS + 1

# QR-Code (Stapel-ID + Seite), oben rechts
QR_BOX_MM = (170.0, 10.0, 190.0, 30.0)
# Logo links neben dem QR – weit weg von den Eckmarken (> 30 mm)
LOGO_BOX_MM = (112.0, 13.0, 164.0, 30.0)

# Kanonische Auflösung nach Perspektivkorrektur
CANONICAL_DPI = 200

# Layout-Version im Spec-Snapshot: fehlt sie (Altbestand) → v1 = altes festes Raster
LAYOUT_VERSION = 3   # v3: Kopfzeilenhöhe wächst mit dem längsten Label (bis HEAD_MAX_LINES)

# ---------- v2: Tabellen-Design ----------
CONTENT_X0, CONTENT_X1 = 20.0, 190.0
TABLE_Y0 = 57.0          # Oberkante der ersten Tabellen-Kopfzeile
TABLE_Y1 = 277.0         # Unterkante der Tabelle (darunter Fußzeile)
HEAD_H = 7.5             # Höhe der schwarzen Kopfzeile
HEAD_GAP = 0.8           # Luft zwischen Kopfzeile und erster Bubble-Zeile
GROUP_GAP = 3.0          # Luft vor jeder weiteren Tabelle
COL_W = 14.0             # Breite einer Bubble-Spalte
HEAD_FONT, HEAD_SIZE = "Helvetica-Bold", 5.6   # Optionslabels in der Kopfzeile
HEAD_LINE_H = 2.35       # Zeilenabstand der Labels
HEAD_MAX_LINES = 6
BUBBLE_R_MM = 2.5
PITCH_MIN = 2 * BUBBLE_R_MM + 1.5   # ≥ 1,5 mm zwischen Kreisen benachbarter Zeilen
PITCH_MAX = 8.5
# Seite 2: Freitextkasten (Überschrift darüber, Kasten füllt den Rest der Seite)
FREE_HEAD_Y = 61.0       # Grundlinie der Überschrift
FREE_BOX_MM = (CONTENT_X0, 66.0, CONTENT_X1, 277.0)

NOT_FIT_MSG = ("Fragebogen passt nicht auf eine Seite – weniger Fragen oder weniger "
               "unterschiedliche Antwortsätze")


def pts(width_mm: float) -> float:
    return width_mm / 25.4 * 72.0


def fit_text(text: str, font: str, size: float, max_width_mm: float) -> str:
    """Kürzt Text mit Ellipse, damit er exakt in max_width_mm passt."""
    max_pts = pts(max_width_mm)
    if stringWidth(text, font, size) <= max_pts:
        return text
    while text and stringWidth(text + "…", font, size) > max_pts:
        text = text[:-1]
    return text + "…"


def wrap(text: str, font: str, size: float, width_mm: float,
          max_lines: int) -> tuple[list[str], bool]:
    """Wortweiser Umbruch; (Zeilen, passt_vollständig). Rest → Ellipse in der letzten Zeile."""
    max_pts, lines, cur = pts(width_mm), [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if stringWidth(cand, font, size) <= max_pts:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = word
    if cur:
        lines.append(cur)
    ok = len(lines) <= max_lines and all(stringWidth(s, font, size) <= max_pts for s in lines)
    if not ok:
        lines = lines[:max_lines - 1] + [fit_text(" ".join(lines[max_lines - 1:]), font,
                                                    size, width_mm)]
    return lines, ok


def head_height(labels: list[str]) -> float:
    """v3: Kopfzeilenhöhe nach Zeilenzahl des längsten Labels (2 Zeilen = HEAD_H)."""
    n = max(len(wrap(lb, HEAD_FONT, HEAD_SIZE, COL_W - 1.2, HEAD_MAX_LINES)[0]) for lb in labels)
    return max(HEAD_H, HEAD_LINE_H * n + 2.8)


@dataclass
class Row:
    y: float                              # Zeilenmitte
    bubbles: list[tuple[float, float]]    # Zentren, letzte = "Keine Angabe"
    text_x1: float                        # Ende der Fragetext-Zone


@dataclass
class Header:
    y0: float
    y1: float
    labels: list[str]
    xs: list[float]                       # Spaltenmitten


@dataclass
class Layout:
    version: int
    bubble_r: float
    pitch: float
    rows: list[Row]
    headers: list[Header] = field(default_factory=list)


def col_x(col: int) -> float:
    return CONTENT_X1 - (MAX_BUBBLES - col - 0.5) * COL_W


def _legacy_layout(spec) -> Layout:
    """v1 (bis Layout-Version 2): festes Raster, nur für bereits gedruckte Stapel."""
    rows = []
    for i, q in enumerate(spec.questions):
        y = 46.0 + i * 7.8
        n = max(2, min(len(q.answers()), MAX_BUBBLES))
        rows.append(Row(y, [(110.0 + j * 15.5, y) for j in range(n)], 104.0))
    return Layout(1, 2.6, 7.8, rows)


def compute_layout(spec) -> Layout:
    """Alle variablen Positionen aus der Spec. ValueError, wenn sie nicht auf Seite 1 passt.

    Je Gruppe aufeinanderfolgender Fragen mit identischen Antworten eine Kopfzeile;
    Spalten rechtsbündig, "Keine Angabe" ist immer die letzte (rechte) Spalte."""
    if getattr(spec, "layout_version", LAYOUT_VERSION) < 2:
        return _legacy_layout(spec)
    groups: list[list] = []
    for q in spec.questions:
        if groups and groups[-1][0].answers() == q.answers():
            groups[-1].append(q)
        else:
            groups.append([q])
    v3 = getattr(spec, "layout_version", LAYOUT_VERSION) >= 3
    heads = [head_height(g[0].answers()) if v3 else HEAD_H for g in groups]
    fixed = sum(heads) + len(groups) * HEAD_GAP + (len(groups) - 1) * GROUP_GAP
    pitch = min(PITCH_MAX, (TABLE_Y1 - TABLE_Y0 - fixed) / max(1, len(spec.questions)))
    if pitch < PITCH_MIN:
        raise ValueError(NOT_FIT_MSG)
    rows, headers, y = [], [], TABLE_Y0
    for gi, g in enumerate(groups):
        labels = g[0].answers()
        xs = [col_x(MAX_BUBBLES - len(labels) + j) for j in range(len(labels))]
        y += GROUP_GAP if gi else 0.0
        headers.append(Header(y, y + heads[gi], labels, xs))
        y += heads[gi] + HEAD_GAP
        for _q in g:
            cy = y + pitch / 2
            rows.append(Row(cy, [(x, cy) for x in xs], xs[0] - COL_W / 2 - 1.5))
            y += pitch
    return Layout(3 if v3 else 2, BUBBLE_R_MM, pitch, rows, headers)


def mm_to_px(mm: float, dpi: int = CANONICAL_DPI) -> float:
    return mm * dpi / 25.4


def canonical_size_px(dpi: int = CANONICAL_DPI) -> tuple[int, int]:
    return (round(mm_to_px(PAGE_W_MM, dpi)), round(mm_to_px(PAGE_H_MM, dpi)))


def mark_centers_px(dpi: int = CANONICAL_DPI) -> list[tuple[float, float]]:
    return [(mm_to_px(x, dpi), mm_to_px(y, dpi)) for x, y in MARK_CENTERS_MM]
