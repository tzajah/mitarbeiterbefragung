"""Diagnose 3: Bubble-Tintenwerte für Zeile 0 nach dem Warp."""
import cv2
import numpy as np

from app.formgen.generator import render_pdf
from app.formgen.layout import bubble_centers_mm, mm_to_px
from app.formgen.spec import default_spec
from app.formgen.stack_id import new_stack_id
from app.scan.reader import _disk_ink, _mark_candidates, _select_quad, _warp
from app.scan.synth import fill_form, render_pdf_to_image, simulate_scan

spec = default_spec()
pdf = render_pdf(spec, new_stack_id())
rng = np.random.default_rng(42)
answers = {q.id: int(rng.integers(0, 5)) if rng.random() > 0.07 else None
           for q in spec.questions}
print("Ground truth q01:", answers["q01"])
img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
scan_bytes = simulate_scan(img, rng=rng)
scan = cv2.imdecode(np.frombuffer(scan_bytes, np.uint8), cv2.IMREAD_COLOR)

gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
bw = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255,
                   cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
cands = _mark_candidates(bw)
quad, score = _select_quad(cands, gray.shape)
print("quad:", quad.tolist(), "score:", score)
warped = _warp(scan, quad)
cv2.imwrite("data/diag_warped.png", warped)
wg = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

r = mm_to_px(2.6)
r_in, r_bg = int(round(r * 0.60)), int(round(r * 1.45))
print("r, r_in, r_bg:", r, r_in, r_bg)
for j in range(5):
    xmm, ymm = bubble_centers_mm(0, 5)[j]
    cx, cy = int(round(mm_to_px(xmm))), int(round(mm_to_px(ymm)))
    ink = _disk_ink(wg, cx, cy, r_in, r_bg)
    print(f"  q01 col {j} ('{spec.questions[0].answers()[j]}'): ink={ink:.3f} @({cx},{cy})")

# Zoom-Crop um Bubble 3 speichern
xmm, ymm = bubble_centers_mm(0, 5)[3]
cx, cy = int(round(mm_to_px(xmm))), int(round(mm_to_px(ymm)))
crop = warped[max(0, cy - 60):cy + 60, max(0, cx - 60):cx + 60]
cv2.imwrite("data/diag_bubble.png", crop)
