"""Round-Trip: synthetisch ausgefüllte, verunstaltete Bögen → korrekt erkannt?"""
import numpy as np
import pytest

from app.formgen.generator import render_pdf
from app.formgen.spec import default_spec
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


def test_garbage_raises(spec):
    with pytest.raises(ValueError):
        read_scan(b"kein bild", spec)


def spec_q_label(spec, qid, idx):
    q = next(q for q in spec.questions if q.id == qid)
    return None if idx is None else q.answers()[idx]
