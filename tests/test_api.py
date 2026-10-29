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
    monkeypatch.setattr(cfg, "SPEC_PATH", tmp_path / "spec.json")
    # Pfade in main(config)-Nutzern auf tmp umlenken
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "SCANS_DIR", tmp_path / "scans")
    monkeypatch.setattr(main_mod, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(main_mod, "SPEC_PATH", tmp_path / "spec.json")
    monkeypatch.setattr(main_mod, "LOGO_PATH", tmp_path / "logo.png")
    from app.db import DB_PATH as _orig  # noqa: F401  (nur Modulkontext)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    with TestClient(app) as c:
        yield c


def _make_scan_for_stack(pdf_bytes: bytes, rng, spec=None) -> tuple[bytes, dict[str, str]]:
    spec = spec or default_spec()
    answers = {q.id: int(rng.integers(0, len(q.real_options()))) for q in spec.questions}
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


def test_demo_scan_lands_in_review(client):
    stack = client.post("/api/stacks", json={"department": "Demo-Abteilung"}).json()
    r = client.post(f"/api/stacks/{stack['stack_id']}/demo")
    assert r.status_code == 200, r.text
    demo = r.json()
    assert demo["stack_id"] == stack["stack_id"]
    assert demo["quad_score"] > 0.9
    assert len(demo["answers"]) == 25
    # unfinalisiert → muss in der Review-Liste auftauchen
    open_rows = client.get("/api/responses?finalized=false").json()
    assert any(row["id"] == demo["response_id"] for row in open_rows)


# ---------- Eigene Fragen ----------

def _custom_body():
    return {"title": "Eigener Bogen", "free_text_prompt": "Was noch?", "questions": [
        {"text": "  Ich arbeite gern hier.  ", "kind": "scale",
         "scale_labels": ["nie", "selten", "oft"]},
        {"text": "Wie kommst du zur Arbeit?", "kind": "choice",
         "options": ["Auto", "Bahn", "Rad"]},
        {"text": "Passt die Ausstattung?", "kind": "scale",
         "scale_labels": ["ja", "nein"]},
    ]}


def test_custom_spec_snapshot_per_stack(client):
    rng = np.random.default_rng(5)
    put = client.put("/api/spec", json=_custom_body())
    assert put.status_code == 200, put.text
    spec = put.json()
    assert [q["id"] for q in spec["questions"]] == ["q01", "q02", "q03"]
    assert spec["questions"][0]["text"] == "Ich arbeite gern hier."  # getrimmt
    assert client.get("/api/spec").json() == spec

    stack = client.post("/api/stacks", json={"department": "A"}).json()
    pdf = client.get(stack["pdf_url"]).content
    from app.formgen.spec import SurveySpec
    old = SurveySpec.from_dict(spec)
    scan, truth = _make_scan_for_stack(pdf, rng, old)
    up = client.post("/api/scans", files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    assert {a["question_id"]: a["value"] for a in up["answers"]} == truth
    detail = client.get(f"/api/responses/{up['response_id']}").json()
    assert detail["spec"]["questions"][1]["answers"] == ["Auto", "Bahn", "Rad", "Keine Angabe"]
    fin = client.post(f"/api/responses/{up['response_id']}/finalize",
                      json={"stack_id": stack["stack_id"], "corrections": {"q02": "Bahn"}})
    assert fin.status_code == 200

    # Spec ändern: alter Stapel liest weiter mit seinem Snapshot
    body = _custom_body()
    body["questions"] = body["questions"][:1] + [
        {"text": "Neu", "kind": "choice", "options": ["x", "y"]}]
    assert client.put("/api/spec", json=body).status_code == 200
    scan2, truth2 = _make_scan_for_stack(pdf, rng, old)
    up2 = client.post("/api/scans", files={"file": ("s.jpg", scan2, "image/jpeg")}).json()
    assert {a["question_id"]: a["value"] for a in up2["answers"]} == truth2
    bad = client.post(f"/api/responses/{up2['response_id']}/finalize",
                      json={"stack_id": stack["stack_id"], "corrections": {"q02": "x"}})
    assert bad.status_code == 422  # q02 des alten Stapels kennt "x" nicht

    # Auswertung zeigt die Fragetexte des Stapels (2. finalisierter Bogen nötig für k=2)
    scan3, _ = _make_scan_for_stack(pdf, rng, old)
    up3 = client.post("/api/scans", files={"file": ("s.jpg", scan3, "image/jpeg")}).json()
    client.post(f"/api/responses/{up3['response_id']}/finalize",
                json={"stack_id": stack["stack_id"]})
    res = client.get("/api/results?k=2").json()
    assert res["departments"][0]["questions"][1] == {"id": "q02", "text": "Wie kommst du zur Arbeit?"}


def test_spec_validation_and_reset(client):
    def put(**changes):
        b = _custom_body()
        q0 = b["questions"][0]
        for k, v in changes.items():
            if k == "questions":
                b["questions"] = v
            else:
                q0[k] = v
        return client.put("/api/spec", json=b).status_code

    many = [{"text": "f", "kind": "scale", "scale_labels": ["a", "b"]}] * 31
    assert put(questions=many) == 422
    assert put(questions=[]) == 422
    assert put(scale_labels=["nur eine"]) == 422
    assert put(scale_labels=["a", "Keine Angabe"]) == 422
    assert put(scale_labels=["a", "A"]) == 422
    assert put(scale_labels=["a", "b", "c", "d", "e", "f"]) == 422
    assert put(text="   ") == 422
    assert put(kind="matrix") == 422
    assert put(scale_labels=["a", "x" * 40]) == 422
    assert put() == 200
    assert client.post("/api/spec/reset").json()["title"] == default_spec().title
    assert client.get("/api/spec").json()["questions"][0]["id"] == "q01"
    assert len(client.get("/api/spec").json()["questions"]) == 25


# ---------- Auth (Server-Betrieb) ----------

def _auth_client(monkeypatch, tmp_path):
    """TestClient mit aktiviertem Login (APP_PASSWORD gesetzt)."""
    import os
    monkeypatch.setenv("APP_PASSWORD", "test-1234")
    monkeypatch.setenv("APP_USER", "chef")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_auth_disabled_by_default(monkeypatch, tmp_path):
    for var in ("APP_PASSWORD", "APP_PASSWORD_HASH", "APP_SECRET"):
        monkeypatch.delenv(var, raising=False)
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True, "auth": False}
        assert c.get("/api/stacks").status_code == 200  # offen wie gehabt


def test_auth_blocks_and_login_works(monkeypatch, tmp_path):
    for var in ("APP_PASSWORD_HASH", "APP_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_PASSWORD", "test-1234")
    monkeypatch.setenv("APP_USER", "chef")
    from app.main import app
    with TestClient(app) as c:
        # Geschützt: ohne Login 401 (API) bzw. Redirect (Seiten)
        r = c.get("/api/stacks")
        assert r.status_code == 401
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and "/login.html" in r.headers["location"]
        # Falsches Passwort
        assert c.post("/api/login", json={"username": "chef", "password": "falsch"}
                      ).status_code == 401
        # Richtiges Passwort → Cookie → Zugriff
        ok = c.post("/api/login", json={"username": "chef", "password": "test-1234"})
        assert ok.status_code == 200 and "mbf_session" in ok.cookies
        assert c.get("/api/stacks").status_code == 200
        assert c.get("/api/results").status_code == 200
        for path in ("/api/logo", "/api/spec/preview.pdf"):  # neue Routen geschützt
            c.cookies.clear()
            assert c.get(path).status_code == 401
            c.post("/api/login", json={"username": "chef", "password": "test-1234"})
        # Logout räumt auf
        c.post("/api/logout")
        assert c.get("/api/stacks").status_code == 401


def test_password_hash_mode(monkeypatch):
    import hashlib
    salt = os.urandom(16).hex() if (os := __import__("os")) else ""
    h = (f"scrypt$16384$8$1${salt}$"
         f"{hashlib.scrypt(b'geheim', salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()}")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("APP_PASSWORD_HASH", h)
    monkeypatch.setenv("APP_USER", "admin")
    from app.main import app
    with TestClient(app) as c:
        assert c.post("/api/login", json={"username": "admin", "password": "geheim"}
                      ).status_code == 200
        assert c.post("/api/login", json={"username": "admin", "password": "falsch"}
                      ).status_code == 401


def test_session_tampering_rejected(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "test-1234")
    from app.main import app
    with TestClient(app) as c:
        c.post("/api/login", json={"username": "admin", "password": "test-1234"})
        good = c.cookies.get("mbf_session")
        assert good
        tampered = good[:-4] + ("aaaa" if good[-4:] != "aaaa" else "bbbb")
        c.cookies.set("mbf_session", tampered)
        assert c.get("/api/stacks").status_code == 401


# ---------- Zweiseitiger Bogen, PDF-Import, Logo ----------

def _sheet_scans(pdf_bytes, rng, spec=None):
    """Beide Seiten eines Bogens als synthetische Scans (S. 2 mit Gekritzel)."""
    from app.scan.synth import scribble
    s1, truth = _make_scan_for_stack(pdf_bytes, rng, spec)
    s2 = simulate_scan(scribble(render_pdf_to_image(pdf_bytes, page=1), rng), rng=rng)
    return s1, s2, truth


def _pdf_of(images: list[bytes]) -> bytes:
    from PIL import Image
    pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    buf = io.BytesIO()
    pil[0].save(buf, "PDF", save_all=True, append_images=pil[1:], resolution=150)
    return buf.getvalue()


def test_pdf_upload_duplex(client):
    rng = np.random.default_rng(31)
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(stack["pdf_url"]).content
    a1, a2, truth_a = _sheet_scans(pdf, rng)
    b1, b2, truth_b = _sheet_scans(pdf, rng)
    blank = simulate_scan(np.full((1754, 1240, 3), 255, np.uint8), rng=rng)
    up = client.post("/api/scans", files={"file": ("stapel.pdf", _pdf_of([a1, a2, blank, b1, b2]),
                                                   "application/pdf")})
    assert up.status_code == 200, up.text
    pages = up.json()["pages"]
    assert [p.get("sheet_page") for p in pages] == [1, 2, None, 1, 2]
    assert "Eckmarken" in pages[2]["error"]  # Leerseite: Fehler nur für diese Seite
    for p1, truth in ((pages[0], truth_a), (pages[3], truth_b)):
        assert p1["stack_id"] == stack["stack_id"]
        assert {a["question_id"]: a["value"] for a in p1["answers"]} == truth
        assert p1["image2_url"] and "keine Freitextseite gefunden" not in p1["warnings"]
        img2 = client.get(p1["image2_url"])
        assert img2.status_code == 200 and img2.content[:3] == bytes([0xFF, 0xD8, 0xFF])
        detail = client.get(f"/api/responses/{p1['response_id']}").json()
        assert detail["image2_url"] == p1["image2_url"]
    assert pages[1]["response_id"] == pages[0]["response_id"]
    assert client.get(f"/api/responses/{'0' * 32}/image?page=2").status_code == 404


def test_single_pages(client):
    rng = np.random.default_rng(32)
    stack = client.post("/api/stacks", json={"department": "HR"}).json()
    pdf = client.get(stack["pdf_url"]).content
    s1, s2, truth = _sheet_scans(pdf, rng)
    only2 = client.post("/api/scans", files={"file": ("s2.jpg", s2, "image/jpeg")})
    assert only2.status_code == 422 and "ohne zugehörige Fragenseite" in only2.json()["detail"]
    only1 = client.post("/api/scans", files={"file": ("s1.jpg", s1, "image/jpeg")}).json()
    assert {a["question_id"]: a["value"] for a in only1["answers"]} == truth
    assert "keine Freitextseite gefunden" in only1["warnings"] and only1["image2_url"] is None
    # mehrere Bilddateien in EINEM Upload werden wie Duplex-Seiten gepaart
    both = client.post("/api/scans", files=[("file", ("a.jpg", s1, "image/jpeg")),
                                            ("file", ("b.jpg", s2, "image/jpeg"))]).json()
    assert both["pages"][0]["image2_url"]


def test_logo_upload_and_preview(client):
    from PIL import Image
    assert client.get("/api/logo").status_code == 404
    assert client.post("/api/logo", files={"file": ("x.png", b"kein bild", "image/png")}
                       ).status_code == 422
    assert client.post("/api/logo", files={"file": ("x.png", b"0" * (2 * 1024 * 1024 + 1),
                                                    "image/png")}).status_code == 413
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1000), (0, 150, 94)).save(buf, "JPEG")
    ok = client.post("/api/logo", files={"file": ("l.jpg", buf.getvalue(), "image/jpeg")})
    assert ok.status_code == 200 and ok.json()["width"] == 1200  # verkleinert
    assert client.get("/api/logo").content[:4] == bytes([0x89]) + b"PNG"
    prev = client.get("/api/spec/preview.pdf")
    assert prev.status_code == 200 and prev.content[:5] == b"%PDF-"
    import pypdfium2 as pdfium
    assert len(pdfium.PdfDocument(prev.content)) == 2
    assert client.delete("/api/logo").status_code == 200
    assert client.get("/api/logo").status_code == 404


def test_spec_design_fields_and_fit(client):
    body = _custom_body() | {"org_title": "BETRIEBSRAT", "org_subtitle": "Musterfirma · Musterstadt",
                             "accent_color": "#123abc", "intro": "Bitte ankreuzen."}
    r = client.put("/api/spec", json=body)
    assert r.status_code == 200 and r.json()["accent_color"] == "#123abc"
    assert client.get("/api/spec").json()["org_subtitle"] == "Musterfirma · Musterstadt"
    assert client.put("/api/spec", json=body | {"accent_color": "grün"}).status_code == 422
    assert client.put("/api/spec", json=body | {"org_title": "x" * 41}).status_code == 422
    assert client.put("/api/spec", json=body | {"intro": "lang " * 60}).status_code == 422
    one_set = [{"text": f"F{i}", "kind": "scale", "scale_labels": ["a", "b", "c", "d", "e"]}
               for i in range(30)]
    assert client.put("/api/spec", json=body | {"questions": one_set}).status_code == 200
    mixed = [q | {"scale_labels": ["ja", "nein"]} if i % 2 else q for i, q in enumerate(one_set)]
    bad = client.put("/api/spec", json=body | {"questions": mixed})
    assert bad.status_code == 422 and "passt nicht auf eine Seite" in bad.json()["detail"]
