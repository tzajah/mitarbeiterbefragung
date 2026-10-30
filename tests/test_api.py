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
    monkeypatch.setattr(main_mod, "SPECS_DIR", tmp_path / "specs")
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
    # getrennte Bilddateien werden NICHT gepaart (Paarung nur innerhalb einer Datei)
    both = client.post("/api/scans", files=[("file", ("a.jpg", s1, "image/jpeg")),
                                            ("file", ("b.jpg", s2, "image/jpeg"))]).json()
    assert both["pages"][0]["image2_url"] is None and "error" in both["pages"][1]


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


# ---------- Review-Findings: Versionen, Upload-Limit, NULL-Stapel, Seitenfehler ----------

def _upload_finalize(client, stack, pdf, rng, spec=None, free_text=None):
    scan, _ = _make_scan_for_stack(pdf, rng, spec)
    up = client.post("/api/scans", files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    fin = client.post(f"/api/responses/{up['response_id']}/finalize",
                      json={"stack_id": stack["stack_id"], "free_text": free_text})
    assert fin.status_code == 200, fin.text
    return up


def test_k_anonymity_per_questionnaire_version(client):
    rng = np.random.default_rng(41)
    old_stack = client.post("/api/stacks", json={"department": "IT"}).json()
    old_pdf = client.get(old_stack["pdf_url"]).content
    for _ in range(4):
        _upload_finalize(client, old_stack, old_pdf, rng)
    body = _custom_body()
    assert client.put("/api/spec", json=body).status_code == 200
    new_stack = client.post("/api/stacks", json={"department": "IT"}).json()
    new_pdf = client.get(new_stack["pdf_url"]).content
    from app.formgen.spec import SurveySpec
    _upload_finalize(client, new_stack, new_pdf, rng,
                     SurveySpec.from_dict(client.get("/api/spec").json()), "geheim")
    deps = client.get("/api/results?k=5").json()["departments"]
    assert len(deps) == 2 and sorted(d["n"] for d in deps) == [1, 4]
    assert all(d["suppressed"] for d in deps), "4 alt + 1 neu darf k=5 nicht erfüllen"
    assert all("free_texts" not in d and d["version"] for d in deps)


def test_optics_change_keeps_fingerprint(client):
    a = client.get("/api/spec").json()
    from app.formgen.spec import SurveySpec
    s1 = SurveySpec.from_dict(a)
    s2 = SurveySpec.from_dict(a | {"title": "anders", "accent_color": "#123456"})
    assert s1.fingerprint() == s2.fingerprint()
    s3 = SurveySpec.from_dict(a | {"free_text_prompt": "etwas anderes?"})
    assert s1.fingerprint() != s3.fingerprint()


def test_finalize_rejects_other_questionnaire(client):
    rng = np.random.default_rng(42)
    s_old = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(s_old["pdf_url"]).content
    client.put("/api/spec", json=_custom_body())
    s_new = client.post("/api/stacks", json={"department": "IT"}).json()
    rows = {r["id"]: r for r in client.get("/api/stacks").json()}
    assert rows[s_old["stack_id"]]["spec_hash"] != rows[s_new["stack_id"]]["spec_hash"]
    scan, _ = _make_scan_for_stack(pdf, rng)
    up = client.post("/api/scans", files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    detail = client.get(f"/api/responses/{up['response_id']}").json()
    assert detail["response"]["spec_hash"] == rows[s_old["stack_id"]]["spec_hash"]
    bad = client.post(f"/api/responses/{up['response_id']}/finalize",
                      json={"stack_id": s_new["stack_id"]})
    assert bad.status_code == 409 and "anderen Fragebogen" in bad.json()["detail"]


def test_upload_total_limit(client, monkeypatch):
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "MAX_UPLOAD_BYTES", 1000)
    r = client.post("/api/scans", files=[("file", ("a.jpg", b"x" * 600, "image/jpeg")),
                                         ("file", ("b.jpg", b"x" * 600, "image/jpeg"))])
    assert r.status_code == 413 and "max. 100 MB" in r.json()["detail"]


def test_init_db_migrates_old_schema(tmp_path, monkeypatch):
    import sqlite3
    import app.db as db_mod
    path = tmp_path / "old.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE stacks (id TEXT PRIMARY KEY, department TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE responses (id TEXT PRIMARY KEY, stack_id TEXT NOT NULL REFERENCES stacks(id),
            quad_score REAL, finalized INTEGER NOT NULL DEFAULT 0, free_text TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE answers (response_id TEXT NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL, value TEXT, confidence REAL,
            needs_review INTEGER NOT NULL DEFAULT 0, note TEXT,
            source TEXT NOT NULL DEFAULT 'auto', PRIMARY KEY (response_id, question_id));
        INSERT INTO stacks (id, department) VALUES ('s1', 'IT');
        INSERT INTO responses (id, stack_id, finalized, free_text) VALUES ('r1', 's1', 1, 'hi');
        INSERT INTO answers (response_id, question_id, value) VALUES ('r1', 'q01', 'ja');
    """)
    con.commit()
    con.close()
    for _ in range(2):  # idempotent
        db_mod.init_db()
        with db_mod.get_db() as db:
            assert db.execute("SELECT free_text FROM responses").fetchone()[0] == "hi"
            assert db.execute("SELECT value FROM answers").fetchone()[0] == "ja"
            db.execute("INSERT INTO responses (id, stack_id, spec_hash) VALUES ('r2', NULL, 'h')")
            db.execute("DELETE FROM responses WHERE id = 'r2'")
            assert db.execute("SELECT spec_hash FROM stacks").fetchone()[0] is None


def test_unreadable_qr_stored_unassigned(client):
    from app.formgen.layout import QR_BOX_MM, mm_to_px
    rng = np.random.default_rng(43)
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(stack["pdf_url"]).content
    img = render_pdf_to_image(pdf)
    dpi = 150
    x0, y0, x1, y1 = (int(v / 25.4 * dpi) for v in QR_BOX_MM)
    img = fill_form(img, default_spec(), {q.id: 0 for q in default_spec().questions}, rng=rng)
    img[y0 - 3:y1 + 3, x0 - 3:x1 + 3] = 255  # QR übermalen
    bad = simulate_scan(img, rng=rng)
    good, _ = _make_scan_for_stack(pdf, rng)
    r = client.post("/api/scans", files=[("file", ("bad.jpg", bad, "image/jpeg")),
                                         ("file", ("good.jpg", good, "image/jpeg"))])
    assert r.status_code == 200, r.text
    p = r.json()["pages"]
    assert p[0]["stack_id"] is None and p[0]["response_id"]
    assert "QR-Code nicht lesbar" in p[0]["warnings"][0]
    assert p[1]["stack_id"] == stack["stack_id"]
    detail = client.get(f"/api/responses/{p[0]['response_id']}").json()
    assert detail["response"]["spec_hash"]
    assert client.post(f"/api/responses/{p[0]['response_id']}/finalize", json={}).status_code == 400


def test_broken_pdf_page_does_not_abort(client, monkeypatch):
    import pypdfium2 as pdfium
    rng = np.random.default_rng(44)
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(stack["pdf_url"]).content
    a1, a2, _ = _sheet_scans(pdf, rng)
    b1, b2, _ = _sheet_scans(pdf, rng)
    orig, calls = pdfium.PdfPage.render, []

    def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise pdfium.PdfiumError("kaputte Seite")
        return orig(self, *a, **kw)

    monkeypatch.setattr(pdfium.PdfPage, "render", flaky)
    up = client.post("/api/scans", files={"file": ("s.pdf", _pdf_of([a1, a2, b1, b2]),
                                                   "application/pdf")})
    assert up.status_code == 200, up.text
    pages = up.json()["pages"]
    assert "error" in pages[1] and pages[0]["response_id"]
    assert pages[2]["sheet_page"] == 1 and pages[3]["sheet_page"] == 2


def test_many_pdfs_pair_only_within_file(client):
    rng = np.random.default_rng(45)
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(stack["pdf_url"]).content
    files, s2_alone = [], None
    for i in range(3):
        s1, s2, _ = _sheet_scans(pdf, rng)
        files.append(("file", (f"b{i}.pdf", _pdf_of([s1, s2]), "application/pdf")))
        s2_alone = s2
    files.append(("file", ("nur2.pdf", _pdf_of([s2_alone]), "application/pdf")))
    up = client.post("/api/scans", files=files)
    assert up.status_code == 200, up.text
    pages = up.json()["pages"]
    ok = [p for p in pages if p.get("sheet_page") == 1]
    assert len(ok) == 3 and all(p["image2_url"] for p in ok)
    errs = [p for p in pages if "error" in p]
    assert len(errs) == 1 and errs[0]["file"] == "nur2.pdf" and errs[0]["file_no"] == 3


def test_upload_limit_enforced_on_receive(client, monkeypatch):
    import app.main as main_mod
    from starlette import formparsers
    monkeypatch.setattr(main_mod, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(main_mod, "MULTIPART_OVERHEAD", 0)
    parsed = []
    orig = formparsers.MultiPartParser.parse

    async def spy(self):
        parsed.append(1)
        return await orig(self)
    monkeypatch.setattr(formparsers.MultiPartParser, "parse", spy)
    # Content-Length schon zu groß → Ablehnung ohne Parsing
    r = client.post("/api/scans", files={"file": ("a.jpg", b"x" * 5000, "image/jpeg")})
    assert r.status_code == 413 and "max. 100 MB" in r.json()["detail"] and not parsed
    # ohne Content-Length (chunked) → Abbruch beim Empfang
    body = (b'--B\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n'
            + b"x" * 5000 + b"\r\n--B--\r\n")
    r = client.post("/api/scans", content=iter([body[:2000], body[2000:]]),
                    headers={"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status_code == 413 and "max. 100 MB" in r.json()["detail"]


def test_store_uses_spec_from_read(client):
    """Hash kommt von der beim Lesen verwendeten Spec, nicht von der aktuell aktiven."""
    import app.main as main_mod
    from app.scan.reader import ReadResult
    spec = default_spec()
    res = ReadResult(stack_id=None, quad_score=0.9, answers=[], warnings=[], spec=spec)
    r = main_mod._store_response(res, b"\xff\xd8\xff")  # JPEG-Magic genügt
    h = client.get(f"/api/responses/{r['response_id']}").json()["response"]["spec_hash"]
    assert h == spec.fingerprint()


def test_review_uses_spec_used_at_scan(client):
    import app.main as main_mod
    from app.formgen.layout import QR_BOX_MM
    rng = np.random.default_rng(7)
    choice = {"title": "A", "free_text_prompt": "F",
              "questions": [{"text": "Q?", "kind": "choice", "options": ["ja", "nein"]}]}
    assert client.put("/api/spec", json=choice).status_code == 200
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    pdf = client.get(stack["pdf_url"]).content
    img = fill_form(render_pdf_to_image(pdf), main_mod._active_spec(), {"q01": 0}, rng=rng)
    x0, y0, x1, y1 = (int(v / 25.4 * 150) for v in QR_BOX_MM)
    img[y0 - 3:y1 + 3, x0 - 3:x1 + 3] = 255  # QR übermalen → Stapel unbekannt
    scan = simulate_scan(img, rng=rng)
    up = client.post("/api/scans", files={"file": ("s.jpg", scan, "image/jpeg")}).json()
    assert up["stack_id"] is None
    other = {**choice, "questions": [{"text": "Q?", "kind": "choice",
                                      "options": ["rot", "blau", "gelb"]}]}
    assert client.put("/api/spec", json=other).status_code == 200
    d = client.get(f"/api/responses/{up['response_id']}").json()
    assert d["spec"]["questions"][0]["answers"][:2] == ["ja", "nein"]


def test_body_limit_respects_root_path(client):
    with TestClient(app, root_path="/x") as c:
        r = c.post("/x/api/logo", content=b"x" * (3 * 1024 * 1024 + 1),
                   headers={"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status_code == 413


def test_response_spec_fallbacks(client):
    import app.main as main_mod
    a = default_spec()
    a.free_text_prompt = "Fragebogen A"
    b = default_spec()
    b.free_text_prompt = "ganz anders"
    ha = a.fingerprint()
    # Altbestand-Scan mit Hash A: data/specs fehlt, Stapel-Snapshot mit A vorhanden
    a.save(main_mod.TEMPLATES_DIR / ("f" * 32 + ".json"))
    b.save(main_mod.SPEC_PATH)  # aktive Spec ist B
    assert main_mod._response_spec(ha, None).fingerprint() == ha
    assert (main_mod.SPECS_DIR / f"{ha}.json").exists()  # Treffer nach data/specs geschrieben
    # beschädigte data/specs-Datei → Fallback
    (main_mod.SPECS_DIR / f"{ha}.json").write_text("{kaputt")
    assert main_mod._response_spec(ha, None).fingerprint() == ha
    # kein passender Snapshot, aktive Spec B → 409
    (main_mod.TEMPLATES_DIR / ("f" * 32 + ".json")).unlink()
    (main_mod.SPECS_DIR / f"{ha}.json").unlink()
    b.free_text_prompt = "noch anders"
    b.save(main_mod.SPEC_PATH)
    with pytest.raises(main_mod.HTTPException) as e:
        main_mod._response_spec(ha, None)
    assert e.value.status_code == 409


def test_broken_stack_snapshot_never_falls_back_to_legacy(client):
    import app.main as main_mod
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    sid = stack["stack_id"]
    (main_mod.TEMPLATES_DIR / f"{sid}.json").write_text("{kaputt", encoding="utf-8")
    # Fehler statt Lesen an falschen Kreuzpositionen (kein Alt-Layout, keine Ersatz-Spec)
    with pytest.raises(ValueError):
        main_mod._stack_spec(sid)
    assert client.post(f"/api/stacks/{sid}/demo").status_code == 409


def test_results_skip_stacks_without_hash(client):
    import app.db as db_mod
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    with db_mod.get_db() as db:
        db.execute("INSERT INTO responses (id, stack_id, finalized) VALUES (?, ?, 1)",
                   ("e" * 32, stack["stack_id"]))
        db.execute("UPDATE stacks SET spec_hash = NULL")
    # Backfill schlägt fehl (kaputter Snapshot) → Hash bleibt NULL, Auswertung darf nicht abstürzen
    import app.main as main_mod
    (main_mod.TEMPLATES_DIR / f"{stack['stack_id']}.json").write_text("{", encoding="utf-8")
    r = client.get("/api/results")
    assert r.status_code == 200 and r.json()["departments"] == []


def test_legacy_response_with_broken_snapshot_gives_409(client):
    import app.db as db_mod
    import app.main as main_mod
    stack = client.post("/api/stacks", json={"department": "IT"}).json()
    rid = "d" * 32
    with db_mod.get_db() as db:
        db.execute("INSERT INTO responses (id, stack_id) VALUES (?, ?)", (rid, stack["stack_id"]))
    (main_mod.TEMPLATES_DIR / f"{stack['stack_id']}.json").write_text("{", encoding="utf-8")
    assert client.get(f"/api/responses/{rid}").status_code == 409


def test_require_auth_ohne_passwort_bricht_start_ab(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_REQUIRE_AUTH", "1")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("APP_PASSWORD_HASH", raising=False)
    with pytest.raises(RuntimeError, match="APP_REQUIRE_AUTH ist gesetzt"):
        with TestClient(app):
            pass
    monkeypatch.setenv("APP_PASSWORD", "x")
    monkeypatch.setenv("APP_SECRET", "s")
    import app.main as main_mod
    for n in ("SCANS_DIR", "TEMPLATES_DIR", "SPECS_DIR"):
        monkeypatch.setattr(main_mod, n, tmp_path / n)
    import app.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "t.db")
    with TestClient(app) as c:
        assert c.get("/api/health").json()["auth"] is True


@pytest.mark.parametrize("env, match", [
    ({"APP_REQUIRE_AUTH": " 1 ", "APP_PASSWORD": "   "}, "APP_REQUIRE_AUTH ist gesetzt"),
    ({"APP_REQUIRE_AUTH": "ja"}, "ungültig"),
    ({"APP_REQUIRE_AUTH": "1", "APP_PASSWORD_HASH": "kaputt"}, "scrypt-Format"),
])
def test_auth_config_randfaelle(monkeypatch, env, match):
    from app.auth import check_auth_config
    for k in ("APP_REQUIRE_AUTH", "APP_PASSWORD", "APP_PASSWORD_HASH"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match=match):
        check_auth_config()


def test_password_with_spaces_kept(monkeypatch):
    from app.auth import verify_password
    monkeypatch.delenv("APP_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("APP_USER", raising=False)
    monkeypatch.setenv("APP_PASSWORD", " geheim ")
    assert verify_password("admin", " geheim ") and not verify_password("admin", "geheim")
