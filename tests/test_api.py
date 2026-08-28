"""End-to-End über die API: Stapel → synthetischer Scan → Upload → Finalize → Auswertung."""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.formgen.spec import default_spec
from app.main import app
from app.scan.synth import fill_form, render_pdf_to_image, simulate_scan


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(cfg, "SCANS_DIR", tmp_path / "scans")
    monkeypatch.setattr(cfg, "TEMPLATES_DIR", tmp_path / "templates")
    # Pfade in main(config)-Nutzern auf tmp umlenken
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "SCANS_DIR", tmp_path / "scans")
    monkeypatch.setattr(main_mod, "TEMPLATES_DIR", tmp_path / "templates")
    from app.db import DB_PATH as _orig  # noqa: F401  (nur Modulkontext)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as c:
        yield c


def _make_scan_for_stack(pdf_bytes: bytes, rng) -> tuple[bytes, dict[str, str]]:
    spec = default_spec()
    answers = {q.id: int(rng.integers(0, 5)) for q in spec.questions}
    img = fill_form(render_pdf_to_image(pdf_bytes), spec, answers, rng=rng)
    scan = simulate_scan(img, rng=rng)
    truth = {q.id: q.answers()[answers[q.id]] for q in spec.questions}
    return scan, truth


def test_full_flow(client):
    rng = np.random.default_rng(123)
    # 1. Stapel anlegen
    r = client.post("/api/stacks", json={"department": "IT"})
    assert r.status_code == 200
    stack = r.json()
    assert stack["pdf_url"].endswith(stack["stack_id"] + "/pdf")

    # 2. PDF holen und daraus zwei Scans erzeugen
    pdf = client.get(stack["pdf_url"]).content
    assert pdf[:5] == b"%PDF-"
    scan1, truth1 = _make_scan_for_stack(pdf, rng)
    scan2, truth2 = _make_scan_for_stack(pdf, rng)

    # 3. Upload + automatische Erkennung
    up1 = client.post("/api/scans",
                      files={"file": ("scan1.jpg", scan1, "image/jpeg")}).json()
    assert up1["stack_id"] == stack["stack_id"]
    assert up1["quad_score"] > 0.9
    detected1 = {a["question_id"]: a["value"] for a in up1["answers"]}
    assert detected1 == truth1, "Antworten müssen exakt erkannt werden"
    assert not any(a["needs_review"] for a in up1["answers"])

    up2 = client.post("/api/scans",
                      files={"file": ("scan2.jpg", scan2, "image/jpeg")}).json()
    assert up2["stack_id"] == stack["stack_id"]

    # 4. Finalisieren
    fin = client.post(f"/api/responses/{up1['response_id']}/finalize",
                      json={"stack_id": stack["stack_id"],
                            "free_text": "Mehr Kaffee im Besprechungsraum."})
    assert fin.status_code == 200
    fin2 = client.post(f"/api/responses/{up2['response_id']}/finalize",
                       json={"stack_id": stack["stack_id"], "free_text": ""})
    assert fin2.status_code == 200

    # 5. Auswertung: k=2 → sichtbar, k=3 → unterdrückt
    res2 = client.get("/api/results?k=2").json()
    it = [d for d in res2["departments"] if d["department"] == "IT"][0]
    assert it["n"] == 2 and not it["suppressed"]
    assert it["free_texts"] == ["Mehr Kaffee im Besprechungsraum."]  # leerer Freitext gefiltert
    q01 = it["distributions"]["q01"]
    assert sum(q01.values()) == 2

    res3 = client.get("/api/results?k=3").json()
    it3 = [d for d in res3["departments"] if d["department"] == "IT"][0]
    assert it3["suppressed"]
    assert "distributions" not in it3


def test_finalize_requires_stack(client):
    up = client.post("/api/scans",
                     files={"file": ("x.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")})
    assert up.status_code == 422  # kein gültiges Bild → saubere Fehlermeldung


def test_stapel_liste(client):
    client.post("/api/stacks", json={"department": "Produktion Nord"})
    rows = client.get("/api/stacks").json()
    assert rows and rows[0]["department"] == "Produktion Nord"


def test_double_finalize_blocked(client):
    r = client.post("/api/stacks", json={"department": "IT"})
    stack = r.json()
    fin1 = client.post(f"/api/responses/{'0'*32}/finalize",
                       json={"stack_id": stack["stack_id"]})
    assert fin1.status_code == 404  # unbekannte ID


def test_finalize_double_on_real_response(client):
    rng = np.random.default_rng(77)
    stack = client.post("/api/stacks", json={"department": "HR"}).json()
    pdf = client.get(stack["pdf_url"]).content
    scan, _ = _make_scan_for_stack(pdf, rng)
    up = client.post("/api/scans",
                     files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    rid = up["response_id"]
    ok = client.post(f"/api/responses/{rid}/finalize",
                     json={"stack_id": stack["stack_id"], "free_text": "x"})
    assert ok.status_code == 200
    dup = client.post(f"/api/responses/{rid}/finalize",
                      json={"stack_id": stack["stack_id"]})
    assert dup.status_code == 409, "Doppel-Finalisierung muss blockiert werden"


def test_invalid_correction_rejected(client):
    rng = np.random.default_rng(78)
    stack = client.post("/api/stacks", json={"department": "Logistik"}).json()
    pdf = client.get(stack["pdf_url"]).content
    scan, _ = _make_scan_for_stack(pdf, rng)
    up = client.post("/api/scans",
                     files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    bad = client.post(f"/api/responses/{up['response_id']}/finalize",
                      json={"stack_id": stack["stack_id"],
                            "corrections": {"q01": "Quatsch-Wert"}})
    assert bad.status_code == 422


def test_path_traversal_blocked(client):
    assert client.get("/api/responses/..%5C..%5Cgeheim/image").status_code == 404
    assert client.get("/api/responses/nicht-hex/image").status_code == 404
    assert client.get("/api/stacks/..%2F..%2Fgeheim/pdf").status_code == 404


def test_k_is_clamped(client):
    res = client.get("/api/results?k=1").json()
    assert res["k"] == 2, "k=1 muss auf K_MIN=2 geklemmt werden"
    res = client.get("/api/results?k=99999").json()
    assert res["k"] == 100


def test_department_validation(client):
    assert client.post("/api/stacks", json={"department": "   "}).status_code == 422
    assert client.post("/api/stacks", json={"department": "x" * 81}).status_code == 422


def test_spec_endpoint(client):
    spec = client.get("/api/spec").json()
    assert spec["questions"][0]["answers"]
    assert len(spec["questions"]) == 25
