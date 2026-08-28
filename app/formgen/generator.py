"""Erzeugt das ausfüllbare A4-PDF mit Eckmarken, QR-Code, Bubbles, Freitextfeld.

Koordinaten-Konvention: Alle Layout-Konstanten sind Millimeter von OBEN.
Die einzige Konvertierung in ReportLabs bottom-up-System läuft über Y() –
niemals roh mit * mm auf gemischten Ausdrücken rechnen (Precedence-Falle!).
"""
from __future__ import annotations

import io

import qrcode
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .layout import (
    BUBBLE_PITCH_MM,
    BUBBLE_R_MM,
    BUBBLE_X0_MM,
    MARK_CENTERS_MM,
    MARK_SIZE_MM,
    PAGE_H_MM,
    PAGE_W_MM,
    QR_BOX_MM,
    ROW_PITCH_MM,
    ROW_Y0_MM,
    TEXT_BOX_MM,
    bubble_centers_mm,
)
from .spec import SurveySpec
from .stack_id import qr_payload


def Y(y_mm_from_top: float) -> float:
    """mm von Seitenoberkante → PDF-y (Punkte, von unten)."""
    return (PAGE_H_MM - y_mm_from_top) * mm


def _draw_marks(c: canvas.Canvas) -> None:
    half = MARK_SIZE_MM / 2
    c.setFillColorRGB(0, 0, 0)
    for x, y in MARK_CENTERS_MM:
        c.rect((x - half) * mm, Y(y + half), MARK_SIZE_MM * mm, MARK_SIZE_MM * mm,
               stroke=0, fill=1)


def _qr_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_qr(c: canvas.Canvas, stack_id: str) -> None:
    x0, y0, x1, y1 = QR_BOX_MM
    box_mm = min(x1 - x0, y1 - y0)
    img = qrcode.make(qr_payload(stack_id), border=0)
    c.drawImage(ImageReader(io.BytesIO(_qr_png_bytes(img))),
                x0 * mm, Y(y1), box_mm * mm, box_mm * mm)


def _draw_bubbles(c: canvas.Canvas, spec: SurveySpec) -> None:
    c.setLineWidth(0.9)
    for i, q in enumerate(spec.questions):
        y_top_mm = ROW_Y0_MM + i * ROW_PITCH_MM   # Zeilenmitte von oben
        for j, label in enumerate(q.answers()):
            cx = (BUBBLE_X0_MM + j * BUBBLE_PITCH_MM) * mm
            c.circle(cx, Y(y_top_mm), BUBBLE_R_MM * mm, stroke=1, fill=0)
            if i == 0:
                c.setFont("Helvetica", 4.6)
                c.drawCentredString(cx, Y(y_top_mm - 4.2), label[:14])
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 8)
        c.drawString(16 * mm, Y(y_top_mm + 2.2), q.text[:95])


def _draw_footer(c: canvas.Canvas, spec: SurveySpec) -> None:
    x0, y0, x1, y1 = TEXT_BOX_MM
    c.setFont("Helvetica", 8)
    c.drawString(x0 * mm, Y(y0 - 2.5), f"Freitext: {spec.free_text_prompt}")
    c.rect(x0 * mm, Y(y1), (x1 - x0) * mm, (y1 - y0) * mm)
    c.setFont("Helvetica", 5.5)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.drawString(x0 * mm, Y(290),
                 "Anonym – bitte keinen Namen angeben. Rückgabe in den Sammelkasten deiner Abteilung.")


def render_pdf(spec: SurveySpec, stack_id: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W_MM * mm, PAGE_H_MM * mm))
    c.setTitle(spec.title)
    _draw_marks(c)
    _draw_qr(c, stack_id)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(16 * mm, Y(34), spec.title)
    c.setFont("Helvetica", 7)
    c.drawString(16 * mm, Y(39),
                 "Kreuze in jeder Zeile EINE Antwort an. Weitere Antworten bitte durchstreichen.")
    _draw_bubbles(c, spec)
    _draw_footer(c, spec)
    c.showPage()
    c.save()
    return buf.getvalue()
