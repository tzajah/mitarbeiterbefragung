"""Round-Trip: synthetisch ausgefüllte, verunstaltete Bögen → korrekt erkannt?"""
import numpy as np
import pytest

from app.formgen.generator import render_pdf
from app.formgen.spec import NO_ANSWER, default_spec
from app.formgen.stack_id import new_stack_id
from app.scan.reader import read_scan
from app.scan.synth import fill_form, render_pdf_to_image, simulate_scan


@pytest.fixture(scope="module")
def spec():
    return default_spec()


@pytest.fixture(scope="module")
def pdf_and_id(spec):
    sid = new_stack_id()
    return render_pdf(spec, sid), sid


def test_roundtrip_clean_answers(spec, pdf_and_id):
    pdf, sid = pdf_and_id
    rng = np.random.default_rng(42)
    for trial in range(6):
        # 1–2 Fragen bewusst offen lassen
        answers = {}
        for q in spec.questions:
            answers[q.id] = int(rng.integers(0, 5)) if rng.random() > 0.07 else None
        img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
        scan = simulate_scan(img, rng=rng, jpeg_quality=int(rng.integers(65, 90)))

        result = read_scan(scan, spec)
        assert result.stack_id == sid, f"Trial {trial}: QR falsch ({result.stack_id})"
        assert result.quad_score > 0.9, f"Trial {trial}: Quad-Score {result.quad_score}"
        for reading in result.answers:
            expected = answers[reading.question_id]
            expected_label = spec_q_label(spec, reading.question_id, expected)
            assert reading.value == expected_label, (
                f"Trial {trial} {reading.question_id}: "
                f"erwartet '{expected_label}', erkannt '{reading.value}' ({reading.note})"
            )
            assert not reading.needs_review, (
                f"Trial {trial} {reading.question_id}: unsicher (conf={reading.confidence})"
            )


def test_roundtrip_empty_form(spec, pdf_and_id):
    pdf, sid = pdf_and_id
    rng = np.random.default_rng(7)
    answers = {q.id: None for q in spec.questions}
    img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
    scan = simulate_scan(img, rng=rng)
    result = read_scan(scan, spec)
    assert result.stack_id == sid
    for reading in result.answers:
        assert reading.value is None
        assert not reading.needs_review, f"{reading.question_id}: {reading.note}"


def test_roundtrip_explicit_no_answer(spec, pdf_and_id):
    """Die feste Extra-Spalte 'Keine Angabe' wird zuverlässig erkannt und
    von 'leer gelassen' unterschieden."""
    pdf, sid = pdf_and_id
    rng = np.random.default_rng(99)
    no_idx = len(spec.questions[0].answers()) - 1   # letzte Spalte = Keine Angabe
    for trial in range(4):
        answers = {}
        for i, q in enumerate(spec.questions):
            if i % 3 == 0:
                answers[q.id] = no_idx          # explizit "Keine Angabe" angekreuzt
            elif i % 3 == 1:
                answers[q.id] = int(rng.integers(0, 5))  # echte Antwort
            else:
                answers[q.id] = None            # komplett ausgelassen
        img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
        scan = simulate_scan(img, rng=rng)
        result = read_scan(scan, spec)
        assert result.stack_id == sid
        for reading in result.answers:
            q = next(x for x in spec.questions if x.id == reading.question_id)
            idx = answers[reading.question_id]
            expected = None if idx is None else q.answers()[idx]
            assert reading.value == expected, (
                f"Trial {trial} {reading.question_id}: erwartet {expected!r}, "
                f"erkannt {reading.value!r} ({reading.note})")
            assert not reading.needs_review
        # Mindestens eine Antwort muss explizit "Keine Angabe" sein
        explicit = [r for r in result.answers if r.value == NO_ANSWER]
        assert explicit and all(r.note == "explizit keine Angabe" for r in explicit)


def test_no_answer_is_in_every_question(spec):
    for q in spec.questions:
        assert q.answers()[-1] == NO_ANSWER
        assert len(q.answers()) <= 6  # 5 echte Optionen + Keine Angabe


def test_garbage_raises(spec):
    with pytest.raises(ValueError):
        read_scan(b"kein bild", spec)


def spec_q_label(spec, qid, idx):
    q = next(q for q in spec.questions if q.id == qid)
    return None if idx is None else q.answers()[idx]


# ---------- Neues Tabellen-Layout (v2) ----------

def hard_logo_png() -> bytes:
    """Härtetest: große dunkle Flächen + quadratische schwarze Formen in Eckmarken-Größe."""
    import io
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (520, 170), "white")
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, 170, 170), fill=(0, 150, 94))
    d.rectangle((190, 10, 330, 160), fill=(20, 20, 20))
    for k in range(4):  # 5×5 mm bei 10 px/mm
        d.rectangle((350 + (k % 2) * 90, 20 + (k // 2) * 80, 400 + (k % 2) * 90, 70 + (k // 2) * 80),
                    fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def mixed_spec():
    from app.formgen.spec import Question, SurveySpec
    s5 = ["stimme nicht zu", "eher nein", "neutral", "eher ja", "stimme zu"]
    qs = [Question(f"q{i:02d}", f"Skalenfrage {i}", "scale", scale_labels=s5) for i in range(1, 7)]
    qs.append(Question("q07", "Wie kommst du zur Arbeit?", "choice",
                       options=["Auto", "Bahn", "Fahrrad", "zu Fuß"]))
    qs += [Question(f"q{i:02d}", f"Ja/Nein {i}", "scale", scale_labels=["ja", "nein"])
           for i in range(8, 12)]
    qs.append(Question("q12", "Nochmal Skala", "scale", scale_labels=s5))
    return SurveySpec("Gemischter Bogen", qs, org_subtitle="Musterfirma · Musterstadt")


def test_roundtrip_mixed_spec_with_logo():
    from app.formgen.layout import compute_layout
    spec = mixed_spec()
    assert len(compute_layout(spec).headers) == 4
    sid = new_stack_id()
    pdf = render_pdf(spec, sid, hard_logo_png())
    rng = np.random.default_rng(2026)
    for trial in range(4):
        answers = {q.id: (int(rng.integers(0, len(q.answers()))) if rng.random() > 0.1 else None)
                   for q in spec.questions}
        img = fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng)
        result = read_scan(simulate_scan(img, rng=rng, jpeg_quality=int(rng.integers(65, 90))), spec)
        assert result.stack_id == sid and result.page == 1
        for reading in result.answers:
            assert reading.value == spec_q_label(spec, reading.question_id,
                                                 answers[reading.question_id]), reading
            assert not reading.needs_review
    # Seite 2: nur Entzerrung + QR, keine Bubbles
    p2 = read_scan(simulate_scan(render_pdf_to_image(pdf, page=1), rng=rng), spec)
    assert p2.stack_id == sid and p2.page == 2 and p2.answers == []


def test_page_fit_limits():
    from app.formgen.layout import compute_layout
    from app.formgen.spec import SurveySpec
    s5 = ["a", "b", "c", "d", "e"]
    thirty = {"layout_version": 2, "title": "t", "questions": [
        {"id": f"q{i}", "text": "x", "kind": "scale", "scale_labels": s5} for i in range(30)]}
    SurveySpec.from_dict(thirty)  # 30 Fragen, ein Antwortsatz → passt
    SurveySpec.from_dict({**thirty, "questions": default_spec().to_dict()["questions"]})
    alternating = {**thirty, "questions": [
        {"id": f"q{i}", "text": "x", "kind": "scale", "scale_labels": s5 if i % 2 else ["ja", "nein"]}
        for i in range(30)]}
    with pytest.raises(ValueError, match="passt nicht auf eine Seite"):
        SurveySpec.from_dict(alternating)
    # Altbestand ohne layout_version → altes festes Raster
    legacy = SurveySpec.from_dict({k: v for k, v in thirty.items() if k != "layout_version"})
    assert compute_layout(legacy).rows[0].y == 46.0
