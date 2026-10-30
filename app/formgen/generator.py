"""Erzeugt den zweiseitigen A4-Bogen: Seite 1 Fragen-Tabellen, Seite 2 Freitext.

Beide Seiten tragen Eckmarken + QR-Code (Stapel-ID + Seitennummer).
Koordinaten-Konvention: Alle Layout-Werte sind Millimeter von OBEN.
Die einzige Konvertierung in ReportLabs bottom-up-System läuft über Y() –
niemals roh mit * mm auf gemischten Ausdrücken rechnen (Precedence-Falle!).
"""
from __future__ import annotations

import io

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .layout import (
    COL_W,
    CONTENT_X0,
    CONTENT_X1,
    FREE_BOX_MM,
    FREE_HEAD_Y,
    LOGO_BOX_MM,
    MARK_CENTERS_MM,
    MARK_SIZE_MM,
    PAGE_H_MM,
    PAGE_W_MM,
    QR_BOX_MM,
    HEAD_FONT,
    HEAD_LINE_H,
    HEAD_MAX_LINES,
    HEAD_SIZE,
    compute_layout,
    fit_text as _fit_text,
    pts as _pts,
    wrap as _wrap,
)
from .spec import SurveySpec
from .stack_id import qr_payload

PAGES = 2
GRAY = (0.42, 0.42, 0.42)
LINE_GRAY = (0.78, 0.78, 0.78)
INTRO_SIZE = 7.2
PAGE2_HINT = ("Bitte gut lesbar schreiben (gern in Druckbuchstaben) und keine Namen "
              "nennen – auch nicht den eigenen.")
CONFIDENTIAL = "Vertraulich – anonyme Befragung, keine personenbezogenen Daten"


def Y(y_mm_from_top: float) -> float:
    """mm von Seitenoberkante → PDF-y (Punkte, von unten)."""
    return (PAGE_H_MM - y_mm_from_top) * mm


def label_fits(label: str) -> bool:
    """Passt ein Optionslabel (max. HEAD_MAX_LINES Zeilen) in die Spalte der Kopfzeile?"""
    return _wrap(label, HEAD_FONT, HEAD_SIZE, COL_W - 1.2, HEAD_MAX_LINES, True)[1]


def intro_fits(text: str) -> bool:
    return _wrap(text, "Helvetica", INTRO_SIZE, CONTENT_X1 - CONTENT_X0, 2)[1]


def _fit_size(text: str, font: str, size: float, min_size: float, width_mm: float) -> float:
    w = stringWidth(text, font, size)
    return size if w <= _pts(width_mm) else max(min_size, size * _pts(width_mm) / w)


def _draw_marks(c: canvas.Canvas) -> None:
    half = MARK_SIZE_MM / 2
    c.setFillColorRGB(0, 0, 0)
    for x, y in MARK_CENTERS_MM:
        c.rect((x - half) * mm, Y(y + half), MARK_SIZE_MM * mm, MARK_SIZE_MM * mm,
               stroke=0, fill=1)


def _draw_qr(c: canvas.Canvas, stack_id: str, page: int) -> None:
    x0, y0, x1, y1 = QR_BOX_MM
    box_mm = min(x1 - x0, y1 - y0)
    buf = io.BytesIO()
    qrcode.make(qr_payload(stack_id, page), border=0).save(buf, format="PNG")
    c.drawImage(ImageReader(io.BytesIO(buf.getvalue())), x0 * mm, Y(y1), box_mm * mm, box_mm * mm)


def _draw_logo(c: canvas.Canvas, logo_png: bytes) -> None:
    """Seitenverhältnis erhalten, rechtsbündig + vertikal zentriert in LOGO_BOX_MM."""
    img = ImageReader(io.BytesIO(logo_png))
    iw, ih = img.getSize()
    x0, y0, x1, y1 = LOGO_BOX_MM
    s = min((x1 - x0) / iw, (y1 - y0) / ih)
    w, h = iw * s, ih * s
    top = y0 + ((y1 - y0) - h) / 2
    c.drawImage(img, (x1 - w) * mm, Y(top + h), w * mm, h * mm, mask="auto")


def _rule_pair(c: canvas.Canvas, y: float, accent) -> None:
    """Schwarze durchgezogene Linie + direkt darunter gestrichelte Akzentlinie."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.4)
    c.line(CONTENT_X0 * mm, Y(y), CONTENT_X1 * mm, Y(y))
    c.setStrokeColor(accent)
    c.setLineWidth(1.9)
    c.setDash([3.2 * mm, 1.6 * mm])
    c.line(CONTENT_X0 * mm, Y(y + 2.0), CONTENT_X1 * mm, Y(y + 2.0))
    c.setDash([])
    c.setStrokeColorRGB(0, 0, 0)


def _draw_frame(c: canvas.Canvas, spec: SurveySpec, stack_id: str, page: int,
                logo_png: bytes | None) -> None:
    """Kopf + Fußzeile, identisch auf beiden Seiten."""
    accent = HexColor(spec.accent_color)
    _draw_marks(c)
    _draw_qr(c, stack_id, page)
    if logo_png:
        _draw_logo(c, logo_png)
    left_w = LOGO_BOX_MM[0] - CONTENT_X0 - 3
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(CONTENT_X0 * mm, Y(21.5), _fit_text(spec.org_title, "Helvetica-Bold", 15, left_w))
    c.setFillColorRGB(*GRAY)
    c.setFont("Helvetica", 8.5)
    c.drawString(CONTENT_X0 * mm, Y(26.8), _fit_text(spec.org_subtitle, "Helvetica", 8.5, left_w))
    _rule_pair(c, 33.0, accent)

    title = spec.title.upper()
    size = _fit_size(title, "Helvetica-Bold", 13, 9.5, CONTENT_X1 - CONTENT_X0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", size)
    c.drawString(CONTENT_X0 * mm, Y(42.4),
                 _fit_text(title, "Helvetica-Bold", size, CONTENT_X1 - CONTENT_X0))
    _rule_pair(c, 44.8, accent)

    # Fußzeile
    c.setStrokeColorRGB(*LINE_GRAY)
    c.setLineWidth(0.6)
    c.line(CONTENT_X0 * mm, Y(280.5), CONTENT_X1 * mm, Y(280.5))
    c.setFillColorRGB(*GRAY)
    c.setFont("Helvetica", 7)
    c.drawString(CONTENT_X0 * mm, Y(284.2), _fit_text(spec.title, "Helvetica", 7, 140))
    c.drawRightString(CONTENT_X1 * mm, Y(284.2), f"Seite {page} von {PAGES}")
    c.setFont("Helvetica-Oblique", 6.5)
    c.drawCentredString(PAGE_W_MM / 2 * mm, Y(287.6), CONFIDENTIAL)
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)


def _draw_gray_text(c: canvas.Canvas, text: str) -> None:
    c.setFillColorRGB(*GRAY)
    c.setFont("Helvetica", INTRO_SIZE)
    lines, _ = _wrap(text, "Helvetica", INTRO_SIZE, CONTENT_X1 - CONTENT_X0, 2)
    for k, line in enumerate(lines):
        c.drawString(CONTENT_X0 * mm, Y(51.0 + k * 3.0), line)
    c.setFillColorRGB(0, 0, 0)


def _draw_tables(c: canvas.Canvas, spec: SurveySpec) -> None:
    lay = compute_layout(spec)
    for h in lay.headers:
        c.setFillColorRGB(0, 0, 0)
        c.rect(CONTENT_X0 * mm, Y(h.y1), (CONTENT_X1 - CONTENT_X0) * mm, (h.y1 - h.y0) * mm,
               stroke=0, fill=1)
        mid = (h.y0 + h.y1) / 2
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString((CONTENT_X0 + 2) * mm, Y(mid + 1.0), "Frage")
        c.setFont(HEAD_FONT, HEAD_SIZE)
        for label, x in zip(h.labels, h.xs):
            lines, _ = _wrap(label, HEAD_FONT, HEAD_SIZE, COL_W - 1.2,
                             HEAD_MAX_LINES if lay.version >= 3 else 2, lay.version >= 3)
            if lay.version >= 3:
                base = mid + 0.75 - (len(lines) - 1) * HEAD_LINE_H / 2   # Textblock vertikal zentriert
            else:  # v2-Druckbild unverändert lassen
                base = mid + 0.75 if len(lines) == 1 else mid - 0.45
            for k, line in enumerate(lines):
                c.drawCentredString(x * mm, Y(base + k * HEAD_LINE_H), line)
    c.setFillColorRGB(0, 0, 0)
    for q, row in zip(spec.questions, lay.rows):
        width = row.text_x1 - (CONTENT_X0 + 2)
        c.setFont("Helvetica", 7.2)
        lines, _ = _wrap(q.text, "Helvetica", 7.2, width, 2)
        top = row.y + 0.9 if len(lines) == 1 else row.y - 0.55
        for k, line in enumerate(lines):
            c.drawString((CONTENT_X0 + 2) * mm, Y(top + k * 2.9), line)
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.8)
        for x, y in row.bubbles:
            c.circle(x * mm, Y(y), lay.bubble_r * mm, stroke=1, fill=0)
        c.setStrokeColorRGB(*LINE_GRAY)
        c.setLineWidth(0.45)
        sep = row.y + lay.pitch / 2
        c.line(CONTENT_X0 * mm, Y(sep), CONTENT_X1 * mm, Y(sep))
    c.setStrokeColorRGB(0, 0, 0)


def _draw_free_text(c: canvas.Canvas, spec: SurveySpec) -> None:
    c.setFillColorRGB(0, 0, 0)
    size = _fit_size(spec.free_text_prompt, "Helvetica-Bold", 10.5, 8, CONTENT_X1 - CONTENT_X0)
    c.setFont("Helvetica-Bold", size)
    c.drawString(CONTENT_X0 * mm, Y(FREE_HEAD_Y), _fit_text(
        spec.free_text_prompt, "Helvetica-Bold", size, CONTENT_X1 - CONTENT_X0))
    c.setStrokeColorRGB(*LINE_GRAY)
    c.setLineWidth(0.6)
    c.line(CONTENT_X0 * mm, Y(FREE_HEAD_Y + 2.0), CONTENT_X1 * mm, Y(FREE_HEAD_Y + 2.0))
    x0, y0, x1, y1 = FREE_BOX_MM
    # dezente Schreiblinien
    c.setStrokeColorRGB(0.88, 0.88, 0.88)
    c.setLineWidth(0.4)
    y = y0 + 10.0
    while y < y1 - 4:
        c.line((x0 + 4) * mm, Y(y), (x1 - 4) * mm, Y(y))
        y += 10.0
    c.setStrokeColorRGB(0.45, 0.45, 0.45)
    c.setLineWidth(0.7)
    c.rect(x0 * mm, Y(y1), (x1 - x0) * mm, (y1 - y0) * mm, stroke=1, fill=0)
    c.setStrokeColorRGB(0, 0, 0)


def render_pdf(spec: SurveySpec, stack_id: str, logo_png: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W_MM * mm, PAGE_H_MM * mm))
    c.setTitle(spec.title)
    _draw_frame(c, spec, stack_id, 1, logo_png)
    _draw_gray_text(c, spec.intro)
    _draw_tables(c, spec)
    c.showPage()
    _draw_frame(c, spec, stack_id, 2, logo_png)
    _draw_gray_text(c, PAGE2_HINT)
    _draw_free_text(c, spec)
    c.showPage()
    c.save()
    return buf.getvalue()
