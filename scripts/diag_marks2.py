"""Diagnose 2: Bilder speichern und Kandidaten einzeichnen."""
import cv2
import numpy as np

from app.formgen.generator import render_pdf
from app.formgen.layout import MARK_SIZE_MM, PAGE_W_MM
from app.formgen.spec import default_spec
from app.formgen.stack_id import new_stack_id
from app.scan.reader import _mark_candidates
from app.scan.synth import render_pdf_to_image

spec = default_spec()
pdf = render_pdf(spec, new_stack_id())
img = render_pdf_to_image(pdf)
cv2.imwrite("data/diag_form.png", img)

bw = cv2.threshold(cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0),
                   0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
cands = _mark_candidates(bw)
vis = img.copy()
s_exp = img.shape[1] * (MARK_SIZE_MM / PAGE_W_MM)
for score, c in cands:
    p = (int(c[0]), int(c[1]))
    cv2.rectangle(vis, (p[0] - int(s_exp), p[1] - int(s_exp)),
                  (p[0] + int(s_exp), p[1] + int(s_exp)), (0, 0, 255), 2)
    cv2.putText(vis, f"{score:.2f}", (p[0] + 4, p[1] - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
cv2.imwrite("data/diag_candidates.png", vis)
print("candidates on CLEAN render:", len(cands))
for score, c in cands:
    print(f"  score={score:.3f} center=({c[0]:.0f},{c[1]:.0f})")
