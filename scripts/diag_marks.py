"""Diagnose: Warum findet der Reader die Eckmarken nicht?"""
import numpy as np
import cv2

from app.formgen.generator import render_pdf
from app.formgen.spec import default_spec
from app.formgen.stack_id import new_stack_id
from app.scan.reader import _mark_candidates, _select_quad, MARK_SIZE_MM
from app.formgen.layout import PAGE_W_MM
from app.scan.synth import fill_form, render_pdf_to_image, simulate_scan

spec = default_spec()
pdf = render_pdf(spec, new_stack_id())
rng = np.random.default_rng(42)
answers = {q.id: int(rng.integers(0, 5)) for q in spec.questions}
img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
print("Rendered page:", img.shape)
scan_bytes = simulate_scan(img, rng=rng)
scan = cv2.imdecode(np.frombuffer(scan_bytes, np.uint8), cv2.IMREAD_COLOR)
print("Scan:", scan.shape)

gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
_, bw = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255,
                      cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
print("Otsu white-frac:", float((bw > 0).mean()))

s_exp = scan.shape[1] * (MARK_SIZE_MM / PAGE_W_MM)
print("expected marker size px:", s_exp)

cands = _mark_candidates(bw)
print("candidates:", len(cands))
for score, c in cands[:14]:
    print(f"  score={score:.3f} center=({c[0]:.0f},{c[1]:.0f})")

sel = _select_quad(cands, gray.shape)
print("selected quad:", sel[0].tolist() if sel else None,
      "score:", sel[1] if sel else None)
