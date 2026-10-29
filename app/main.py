"""FastAPI-Backend: Stapel drucken, Scans einlesen, Review, k-anonyme Auswertung."""
from __future__ import annotations

import io
import re
import uuid
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

import cv2
from PIL import Image
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import auth_middleware, register_auth
from .config import K_DEFAULT, LOGO_PATH, SCANS_DIR, SPEC_PATH, SPECS_DIR, TEMPLATES_DIR
from .db import get_db, init_db
from .formgen.generator import intro_fits, label_fits, render_pdf
from .formgen.layout import LAYOUT_VERSION, MAX_OPTIONS, MAX_QUESTIONS, compute_layout
from .formgen.spec import (DEFAULT_ACCENT, DEFAULT_INTRO, DEFAULT_ORG_TITLE, HEX_RE,
                           NO_ANSWER, Question, SurveySpec, default_spec)
from .scan.reader import read_scan

MAX_UPLOAD_BYTES = 100 * 1024 * 1024       # Summe aller Dateien je Request (und je Datei)
UPLOAD_TOO_BIG = "Upload zu groß (max. 100 MB) – bitte in mehreren Teilen hochladen"
MAX_SCAN_PAGES = 1000                     # Seiten je Upload
SCAN_PDF_DPI = 200                        # = CANONICAL_DPI, mehr bringt der Erkennung nichts
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_TOO_BIG = "Logo zu groß (max. 2 MB)"
MULTIPART_OVERHEAD = 1024 * 1024          # Spielraum für Boundaries/Header über dem Nutzlast-Limit
MAX_LOGO_PIXELS = 25_000_000              # Decompression-Bomb-Schutz
LOGO_MAX_SIDE = 1200
PREVIEW_STACK_ID = "0" * 32
NO_P2_WARN = "keine Freitextseite gefunden"
ORPHAN_P2 = ("Freitextseite ohne zugehörige Fragenseite – bitte beide Seiten "
             "zusammen hochladen")
K_MIN, K_MAX = 2, 100
_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_id(value: str) -> bool:
    """IDs sind 32 Hex-Zeichen; blockt Pfad-Traversal über URL-Parameter."""
    return bool(_ID_RE.match(value or ""))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    yield


class BodyLimitMiddleware:
    """Reine ASGI-Middleware: bricht zu große Uploads schon beim Empfang ab – bevor
    Starlette den ganzen Multipart-Body parst (RAM/Platte). Sitzt INNERHALB der Auth,
    nicht eingeloggte Requests bekommen also weiterhin 401, ohne dass der Body gelesen wird."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    @staticmethod
    def _limit(scope) -> tuple[int, str] | None:
        if scope["type"] != "http" or scope["method"] != "POST":
            return None
        if scope["path"] == "/api/scans":
            return MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD, UPLOAD_TOO_BIG
        if scope["path"] == "/api/logo":
            return MAX_LOGO_BYTES + MULTIPART_OVERHEAD, LOGO_TOO_BIG
        return None

    async def __call__(self, scope, receive, send):
        lim = self._limit(scope)
        if lim is None:
            return await self.app(scope, receive, send)
        limit, msg = lim
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            return await JSONResponse({"detail": msg}, status_code=413)(scope, receive, send)
        seen = 0

        async def counting_receive():
            nonlocal seen
            m = await receive()
            if m["type"] == "http.request":
                seen += len(m.get("body", b""))
                if seen > limit:  # läuft im Handler-Kontext → ExceptionMiddleware antwortet 413
                    raise HTTPException(413, msg)
            return m

        await self.app(scope, counting_receive, send)


app = FastAPI(title="Mitarbeiterbefragung", version="0.1.0", lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware)   # zuerst hinzugefügt = innerste Schicht (nach der Auth)
app.middleware("http")(auth_middleware)
register_auth(app)


# ---------- Spec-Auflösung: aktive Spec + Snapshot je Stapel ----------

def _active_spec() -> SurveySpec:
    spec = SurveySpec.load(SPEC_PATH) if SPEC_PATH.exists() else default_spec()
    spec.layout_version = LAYOUT_VERSION  # neue Stapel immer im aktuellen Layout
    return spec


def _logo() -> bytes | None:
    return LOGO_PATH.read_bytes() if LOGO_PATH.exists() else None


def _stack_spec(stack_id: str | None) -> SurveySpec:
    """Spec, mit der der Stapel gedruckt wurde. Altbestand ohne Snapshot → Standard;
    unbekannter/fehlender Stapel → aktive Spec."""
    if stack_id and _valid_id(stack_id):
        snap = TEMPLATES_DIR / f"{stack_id}.json"
        if snap.exists():
            return SurveySpec.load(snap)
        with get_db() as db:
            if db.execute("SELECT 1 FROM stacks WHERE id = ?", (stack_id,)).fetchone():
                legacy = default_spec()
                legacy.layout_version = 1  # vor den Snapshots gedruckt → altes Layout
                return legacy
    return _active_spec()


def _save_spec_snapshot(spec: SurveySpec) -> None:
    """Inhaltsadressiert ablegen ({fingerprint}.json), falls noch nicht vorhanden."""
    h = spec.fingerprint()
    if _HASH_RE.match(h) and not (SPECS_DIR / f"{h}.json").exists():
        SPECS_DIR.mkdir(parents=True, exist_ok=True)
        spec.save(SPECS_DIR / f"{h}.json")


def _response_spec(spec_hash: str | None, stack_id: str | None) -> SurveySpec:
    """Spec, mit der ein Scan gelesen wurde: Hash-Snapshot → Stapel-Snapshot → Standard."""
    if spec_hash and _HASH_RE.match(spec_hash):
        snap = SPECS_DIR / f"{spec_hash}.json"
        if snap.exists():
            return SurveySpec.load(snap)
    return _stack_spec(stack_id)


def _backfill_hashes(db) -> None:
    """Altbestand: Fingerprint aus Snapshot bzw. Standard-Spec nachtragen."""
    for r in db.execute("SELECT id FROM stacks WHERE spec_hash IS NULL").fetchall():
        db.execute("UPDATE stacks SET spec_hash = ? WHERE id = ?",
                   (_stack_spec(r["id"]).fingerprint(), r["id"]))


def _spec_dict(spec: SurveySpec) -> dict:
    d = spec.to_dict()
    for q, qd in zip(spec.questions, d["questions"]):
        qd["answers"] = q.answers()
    return d


# ---------- Stapel ----------

class StackCreate(BaseModel):
    department: str


@app.post("/api/stacks")
def create_stack(body: StackCreate):
    dept = body.department.strip()
    if not dept or len(dept) > 80:
        raise HTTPException(422, "Abteilungsname fehlt oder zu lang (max. 80 Zeichen)")
    stack_id = uuid.uuid4().hex
    spec = _active_spec()
    pdf = render_pdf(spec, stack_id, _logo())  # Logo steckt danach im gespeicherten PDF
    pdf_path = TEMPLATES_DIR / f"{stack_id}.pdf"
    pdf_path.write_bytes(pdf)
    spec.save(TEMPLATES_DIR / f"{stack_id}.json")  # Snapshot: Stapel behält seine Fragen
    _save_spec_snapshot(spec)
    with get_db() as db:
        db.execute("INSERT INTO stacks (id, department, spec_hash) VALUES (?, ?, ?)",
                   (stack_id, dept, spec.fingerprint()))
    return {"stack_id": stack_id, "department": dept,
            "pdf_url": f"/api/stacks/{stack_id}/pdf"}


@app.get("/api/stacks")
def list_stacks():
    with get_db() as db:
        _backfill_hashes(db)
        rows = db.execute("""
            SELECT s.id, s.department, s.created_at, s.spec_hash,
                   (SELECT COUNT(*) FROM responses r
                     WHERE r.stack_id = s.id AND r.finalized = 1) AS n
            FROM stacks s ORDER BY s.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/stacks/{stack_id}/pdf")
def stack_pdf(stack_id: str):
    if not _valid_id(stack_id):
        raise HTTPException(404, "Stapel-PDF nicht gefunden")
    p = TEMPLATES_DIR / f"{stack_id}.pdf"
    if not p.exists():
        raise HTTPException(404, "Stapel-PDF nicht gefunden")
    return FileResponse(p, media_type="application/pdf", filename="befragungsbogen.pdf")


# ---------- Scan-Upload ----------

def _as_jpeg(image_bytes: bytes) -> bytes:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return image_bytes
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def _pdf_pages(data: bytes, name: str, max_pages: int) -> list:
    """PDF → [(Bezeichnung, Lader)]; gerendert wird erst beim Verarbeiten der Seite."""
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        raise HTTPException(422, f"{name}: PDF konnte nicht geöffnet werden")

    if len(doc) > max_pages:  # vor dem Anlegen der Seiteneinträge prüfen
        raise HTTPException(413, f"Zu viele Seiten (max. {MAX_SCAN_PAGES} je Upload)")

    def render(i: int) -> bytes:
        page = doc[i]
        # Riesen-Seitenformate nicht zu Gigapixel-Bitmaps aufblasen
        scale = min(SCAN_PDF_DPI / 72.0, 4000 / max(page.get_size()))
        buf = io.BytesIO()
        page.render(scale=scale).to_pil().convert("RGB").save(buf, "JPEG", quality=90)
        return buf.getvalue()

    return [(f"{name} – Seite {i + 1}", partial(render, i)) for i in range(len(doc))]



@app.post("/api/scans")
async def upload_scan(file: list[UploadFile] = File(...)):
    """Ein oder mehrere Bilder und/oder PDFs (jede PDF-Seite = ein Blatt).

    Reihenfolge zählt: Eine Freitextseite (2) gehört zur unmittelbar vorangehenden
    Fragenseite (1) desselben Stapels IN DERSELBEN DATEI (S1, S2, S1, S2 …).
    Antwort: bei genau EINEM Bild wie bisher das Ergebnis-Objekt (Fehler → HTTP 422),
    sonst {"pages": [...]} mit Ergebnis oder "error" je Seite."""
    pages = []
    single_image = len(file) == 1
    remaining = MAX_UPLOAD_BYTES
    for file_no, f in enumerate(file):
        name = f.filename or "Datei"
        data = await f.read(remaining + 1)  # bricht beim Einlesen ab, sobald die Summe reißt
        remaining -= len(data)
        if remaining < 0:
            raise HTTPException(413, UPLOAD_TOO_BIG)
        if data[:5] == b"%PDF-":
            single_image = False
            pages += [(n, ld, file_no, name)
                      for n, ld in _pdf_pages(data, name, MAX_SCAN_PAGES - len(pages))]
        else:
            pages.append((name, partial(bytes, data), file_no, name))
        if len(pages) > MAX_SCAN_PAGES:
            raise HTTPException(413, f"Zu viele Seiten (max. {MAX_SCAN_PAGES} je Upload)")
    results = _ingest_pages(pages)
    if single_image:
        r = results[0]
        if "error" in r:
            raise HTTPException(422, r["error"])
        return r
    return {"pages": results}


def _ingest_pages(pages: list) -> list[dict]:
    out: list[dict] = []
    open_p1: tuple[str | None, dict] | None = None  # (QR-Stapel, Ergebnis) der letzten Seite 1
    prev_file = None
    for n, (name, load, file_no, file_name) in enumerate(pages, 1):
        entry = {"page": n, "name": name, "file": file_name, "file_no": file_no}
        if file_no != prev_file:  # Seitenpaarung nur innerhalb einer Datei
            open_p1 = None
            prev_file = file_no
        try:
            image_bytes = load()
            pages[n - 1] = None  # Referenz auf Rohdaten/PDF freigeben
            result = read_scan(image_bytes, _stack_spec)
        except ValueError as e:
            out.append({**entry, "error": str(e)})
            open_p1 = None
            continue
        except (RuntimeError, OSError, cv2.error, Image.DecompressionBombError) as e:
            # z. B. PdfiumError (RuntimeError) bei beschädigter PDF-Seite, PIL-Fehler
            out.append({**entry, "error": f"Seite konnte nicht gelesen werden: {e}"})
            open_p1 = None
            continue
        if result.page == 2:
            if open_p1 and result.stack_id and open_p1[0] == result.stack_id:
                p1 = open_p1[1]
                (SCANS_DIR / f"{p1['response_id']}_p2.jpg").write_bytes(_as_jpeg(image_bytes))
                p1["image2_url"] = f"/api/responses/{p1['response_id']}/image?page=2"
                out.append({**entry, "sheet_page": 2, "response_id": p1["response_id"]})
            else:
                out.append({**entry, "error": ORPHAN_P2})
            open_p1 = None
            continue
        r = {**entry, "sheet_page": 1, **_store_response(result, image_bytes)}
        out.append(r)
        open_p1 = (result.stack_id, r)
    for r in out:
        if r.get("sheet_page") == 1 and not r["image2_url"]:
            r["warnings"].append(NO_P2_WARN)
    return out


def _store_response(result, image_bytes: bytes) -> dict:
    response_id = uuid.uuid4().hex
    stack_id = result.stack_id
    with get_db() as db:
        spec_hash = None
        if stack_id:
            _backfill_hashes(db)
            known = db.execute("SELECT spec_hash FROM stacks WHERE id = ?",
                               (stack_id,)).fetchone()
            if known:
                spec_hash = known["spec_hash"]
            else:
                stack_id = None  # unbekannter Stapel → manuell zuordnen
        if spec_hash is None:  # der Reader hat mit dieser Spec gelesen – nicht neu auflösen
            spec_hash = result.spec.fingerprint()
            _save_spec_snapshot(result.spec)
        db.execute(
            "INSERT INTO responses (id, stack_id, quad_score, spec_hash) VALUES (?, ?, ?, ?)",
            (response_id, stack_id, result.quad_score, spec_hash))
        for a in result.answers:
            db.execute(
                "INSERT INTO answers (response_id, question_id, value, confidence,"
                " needs_review, note) VALUES (?, ?, ?, ?, ?, ?)",
                (response_id, a.question_id, a.value, a.confidence,
                 int(a.needs_review), a.note))
    (SCANS_DIR / f"{response_id}.jpg").write_bytes(_as_jpeg(image_bytes))
    return {
        "response_id": response_id,
        "stack_id": stack_id,
        "quad_score": result.quad_score,
        "warnings": result.warnings,
        "answers": [vars(a) for a in result.answers],
        "image_url": f"/api/responses/{response_id}/image",
        "image2_url": None,
    }


# ---------- Demo (Test ohne Drucker/Scanner) ----------

@app.post("/api/stacks/{stack_id}/demo")
def demo_scan(stack_id: str):
    """Erzeugt einen synthetisch ausgefüllten, rotierten Bogen (beide Seiten, S. 2 mit
    Gekritzel) und schickt ihn als zweiseitigen Upload durch die ECHTE Pipeline.
    Der Bogen landet (wie ein echter Scan) unfinalisiert im Review."""
    if not _valid_id(stack_id):
        raise HTTPException(404, "Stapel nicht gefunden")
    pdf_path = TEMPLATES_DIR / f"{stack_id}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "Stapel-PDF nicht gefunden")
    import numpy as np
    import pypdfium2 as pdfium
    from .scan.synth import fill_form, render_pdf_to_image, scribble, simulate_scan
    rng = np.random.default_rng()
    spec = _stack_spec(stack_id)
    pdf = pdf_path.read_bytes()
    answers = {q.id: (int(rng.integers(0, len(q.answers())))
                      if rng.random() > 0.15 else None)
               for q in spec.questions}
    scans = [simulate_scan(fill_form(render_pdf_to_image(pdf), spec, answers, rng=rng), rng=rng)]
    if len(pdfium.PdfDocument(pdf)) > 1:  # Altbestand ist einseitig
        scans.append(simulate_scan(scribble(render_pdf_to_image(pdf, page=1), rng), rng=rng))
    r = _ingest_pages([(f"Demo S{i + 1}", partial(bytes, s), 0, "Demo") for i, s in enumerate(scans)])[0]
    if "error" in r:
        raise HTTPException(422, r["error"])
    return r


# ---------- Spec für das Frontend ----------

@app.get("/api/spec")
def get_spec():
    return _spec_dict(_active_spec())


class QuestionIn(BaseModel):
    text: str = ""
    kind: str = "scale"
    options: list[str] = []
    scale_labels: list[str] = []


class SpecIn(BaseModel):
    title: str = ""
    free_text_prompt: str = ""
    org_title: str = DEFAULT_ORG_TITLE
    org_subtitle: str = ""
    accent_color: str = DEFAULT_ACCENT
    intro: str = DEFAULT_INTRO
    questions: list[QuestionIn] = []


def _spec_from_input(body: SpecIn) -> SurveySpec:
    """Vertrauensgrenze: validiert und trimmt; IDs vergibt der Server nach Position."""
    def need(value: str, label: str, limit: int) -> str:
        v = value.strip()
        if not v:
            raise HTTPException(422, f"{label} darf nicht leer sein")
        if len(v) > limit:
            raise HTTPException(422, f"{label} ist zu lang (max. {limit} Zeichen)")
        return v

    def opt(value: str, label: str, limit: int) -> str:
        v = value.strip()
        if len(v) > limit:
            raise HTTPException(422, f"{label} ist zu lang (max. {limit} Zeichen)")
        return v

    title = need(body.title, "Titel", 80)
    prompt = need(body.free_text_prompt, "Freitext-Frage", 100)
    org_title = opt(body.org_title, "Organisation", 40)
    org_subtitle = opt(body.org_subtitle, "Untertitel", 60)
    intro = opt(body.intro, "Hinweistext", 240)
    if not intro_fits(intro):
        raise HTTPException(422, "Hinweistext passt nicht in zwei Zeilen – bitte kürzen")
    accent = body.accent_color.strip()
    if not HEX_RE.match(accent):
        raise HTTPException(422, "Akzentfarbe muss im Format #RRGGBB angegeben werden")
    if not (1 <= len(body.questions) <= MAX_QUESTIONS):
        raise HTTPException(422, f"Es sind 1–{MAX_QUESTIONS} Fragen erlaubt "
                                 f"(aktuell {len(body.questions)})")
    qs = []
    for i, q in enumerate(body.questions, 1):
        where = f"Frage {i}"
        if q.kind not in ("scale", "choice"):
            raise HTTPException(422, f"{where}: Typ muss Skala oder Auswahl sein")
        text = need(q.text, f"{where}: Fragetext", 100)
        opts = [need(o, f"{where}: Antwortoption", 40)
                for o in (q.options if q.kind == "choice" else q.scale_labels)]
        if not (2 <= len(opts) <= MAX_OPTIONS):
            raise HTTPException(422, f"{where}: 2–{MAX_OPTIONS} Antwortoptionen nötig "
                                     f"(ohne 'Keine Angabe'), aktuell {len(opts)}")
        if len({o.casefold() for o in opts}) != len(opts):
            raise HTTPException(422, f"{where}: Antwortoptionen dürfen sich nicht doppeln")
        for o in opts:
            if o.casefold() == NO_ANSWER.casefold():
                raise HTTPException(422, f"{where}: '{NO_ANSWER}' wird automatisch ergänzt")
            if not label_fits(o):
                raise HTTPException(422, f"{where}: Option '{o}' ist zu lang für die Spalte "
                                         "(max. zwei kurze Zeilen, ca. 2 × 12 Zeichen)")
        qs.append(Question(id=f"q{i:02d}", text=text, kind=q.kind,
                           options=opts if q.kind == "choice" else [],
                           scale_labels=opts if q.kind == "scale" else []))
    spec = SurveySpec(title=title, questions=qs, free_text_prompt=prompt, org_title=org_title,
                      org_subtitle=org_subtitle, accent_color=accent, intro=intro)
    try:
        compute_layout(spec)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return spec


@app.put("/api/spec")
def put_spec(body: SpecIn):
    spec = _spec_from_input(body)
    spec.save(SPEC_PATH)
    return _spec_dict(spec)


@app.post("/api/spec/reset")
def reset_spec():
    SPEC_PATH.unlink(missing_ok=True)
    return _spec_dict(default_spec())


@app.get("/api/spec/preview.pdf")
def spec_preview():
    """Gespeicherte Spec + Logo mit Dummy-Stapel-ID – zum Design-Testen, legt nichts an."""
    return Response(render_pdf(_active_spec(), PREVIEW_STACK_ID, _logo()),
                    media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=vorschau.pdf"})


# ---------- Logo ----------

@app.post("/api/logo")
async def upload_logo(file: UploadFile = File(...)):
    from PIL import Image
    data = await file.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(413, "Logo zu groß (max. 2 MB)")
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ("PNG", "JPEG"):
            raise ValueError("Format")
        if im.width * im.height > MAX_LOGO_PIXELS:  # vor dem Dekodieren prüfen
            raise ValueError("zu viele Pixel")
        im.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "Logo muss ein gültiges PNG- oder JPEG-Bild sein "
                                 "(max. 25 Megapixel)")
    im = im.convert("RGBA")
    im.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
    im.save(LOGO_PATH, "PNG")
    return {"status": "ok", "width": im.width, "height": im.height}


@app.get("/api/logo")
def get_logo():
    if not LOGO_PATH.exists():
        raise HTTPException(404, "Kein Logo hinterlegt")
    return FileResponse(LOGO_PATH, media_type="image/png")


@app.delete("/api/logo")
def delete_logo():
    LOGO_PATH.unlink(missing_ok=True)
    return {"status": "ok"}


# ---------- Review ----------

@app.get("/api/responses")
def list_responses(finalized: bool = False):
    with get_db() as db:
        rows = db.execute("""
            SELECT r.id, r.stack_id, st.department, r.quad_score, r.finalized,
                   r.created_at,
                   (SELECT COUNT(*) FROM answers a
                     WHERE a.response_id = r.id AND a.needs_review = 1
                       AND a.value IS NULL) AS n_open
            FROM responses r LEFT JOIN stacks st ON st.id = r.stack_id
            WHERE r.finalized = ? ORDER BY r.created_at DESC
        """, (int(finalized),)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/responses/{response_id}")
def response_detail(response_id: str):
    if not _valid_id(response_id):
        raise HTTPException(404, "Antwort nicht gefunden")
    with get_db() as db:
        row = db.execute("""
            SELECT r.*, st.department FROM responses r
            LEFT JOIN stacks st ON st.id = r.stack_id WHERE r.id = ?
        """, (response_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Antwort nicht gefunden")
        answers = db.execute(
            "SELECT question_id, value, confidence, needs_review, note, source"
            " FROM answers WHERE response_id = ?", (response_id,)).fetchall()
    has_p2 = (SCANS_DIR / f"{response_id}_p2.jpg").exists()
    return {"response": dict(row), "answers": [dict(a) for a in answers],
            "spec": _spec_dict(_response_spec(row["spec_hash"], row["stack_id"])),
            "image_url": f"/api/responses/{response_id}/image",
            "image2_url": f"/api/responses/{response_id}/image?page=2" if has_p2 else None}


@app.get("/api/responses/{response_id}/image")
def response_image(response_id: str, page: int = 1):
    if not _valid_id(response_id) or page not in (1, 2):
        raise HTTPException(404, "Scan-Bild nicht gefunden")
    p = SCANS_DIR / (f"{response_id}.jpg" if page == 1 else f"{response_id}_p2.jpg")
    if not p.exists():
        raise HTTPException(404, "Scan-Bild nicht gefunden")
    return FileResponse(p, media_type="image/jpeg")


class FinalizeBody(BaseModel):
    stack_id: str | None = None
    free_text: str | None = None
    corrections: dict[str, str | None] = {}  # question_id → finaler Wert


@app.post("/api/responses/{response_id}/finalize")
def finalize_response(response_id: str, body: FinalizeBody):
    if not _valid_id(response_id):
        raise HTTPException(404, "Antwort nicht gefunden")
    with get_db() as db:
        row = db.execute("SELECT finalized, stack_id, spec_hash FROM responses WHERE id = ?",
                         (response_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Antwort nicht gefunden")
        if row["finalized"]:
            raise HTTPException(409, "Bogen ist bereits finalisiert")
        if not body.stack_id:
            raise HTTPException(400, "Stapel-Zuordnung fehlt (Abteilung)")
        _backfill_hashes(db)
        target = db.execute("SELECT spec_hash FROM stacks WHERE id = ?",
                            (body.stack_id,)).fetchone()
        if not target:
            raise HTTPException(400, "Unbekannter Stapel")
        read_hash = row["spec_hash"]
        if not read_hash and row["stack_id"]:  # Altbestand ohne Hash
            read_hash = db.execute("SELECT spec_hash FROM stacks WHERE id = ?",
                                   (row["stack_id"],)).fetchone()["spec_hash"]
        if read_hash and read_hash != target["spec_hash"]:
            raise HTTPException(409, "Dieser Stapel hat einen anderen Fragebogen – "
                                     "Scan passt nicht")
        valid_values = {q.id: set(q.answers()) for q in _response_spec(row["spec_hash"], body.stack_id).questions}
        for qid, value in body.corrections.items():
            if qid not in valid_values:
                raise HTTPException(422, f"Unbekannte Frage: {qid}")
            if value is not None and value not in valid_values[qid]:
                raise HTTPException(422, f"Ungültiger Wert für {qid}: {value!r}")
        for qid, value in body.corrections.items():
            db.execute(
                "UPDATE answers SET value = ?, needs_review = 0, source = 'human'"
                " WHERE response_id = ? AND question_id = ?",
                (value, response_id, qid))
        db.execute(
            "UPDATE responses SET finalized = 1, stack_id = ?, free_text = ?"
            " WHERE id = ?", (body.stack_id, body.free_text, response_id))
    return {"status": "finalized", "response_id": response_id}


# ---------- k-anonyme Auswertung ----------

@app.get("/api/results")
def results(k: int = K_DEFAULT):
    # Schutz vor versehentlicher De-Anonymisierung: k wird auf [K_MIN, K_MAX] geklemmt
    k = max(K_MIN, min(K_MAX, k))
    with get_db() as db:
        _backfill_hashes(db)
        # Gruppe = (Abteilung, Fragebogen-Fingerprint): nur gleiche Fragen werden aggregiert
        groups = db.execute("""
            SELECT st.department AS dept, st.spec_hash AS h, COUNT(*) AS n,
                   MIN(st.created_at) AS since
            FROM responses r JOIN stacks st ON st.id = r.stack_id
            WHERE r.finalized = 1 GROUP BY st.department, st.spec_hash
            ORDER BY st.department, since
        """).fetchall()
        out = []
        for g in groups:
            n = g["n"]
            entry = {"department": g["dept"], "version": f"{g['h'][:8]} · {g['since'][:10]}",
                     "n": n, "suppressed": n < max(k, 1)}
            if not entry["suppressed"]:
                latest = db.execute(
                    "SELECT id FROM stacks WHERE department = ? AND spec_hash = ?"
                    " ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (g["dept"], g["h"])).fetchone()
                entry["questions"] = [{"id": q.id, "text": q.text}
                                      for q in _stack_spec(latest["id"]).questions]
                rows = db.execute("""
                    SELECT a.question_id, a.value, COUNT(*) AS c
                    FROM answers a JOIN responses r ON r.id = a.response_id
                    JOIN stacks st ON st.id = r.stack_id
                    WHERE r.finalized = 1 AND st.department = ? AND st.spec_hash = ?
                    GROUP BY a.question_id, a.value
                """, (g["dept"], g["h"])).fetchall()
                dist: dict[str, dict[str, int]] = {}
                for r in rows:
                    dist.setdefault(r["question_id"], {})[r["value"] or "(keine Antwort)"] = r["c"]
                fts = db.execute("""
                    SELECT r.free_text FROM responses r
                    JOIN stacks st ON st.id = r.stack_id
                    WHERE r.finalized = 1 AND st.department = ? AND st.spec_hash = ?
                      AND r.free_text IS NOT NULL AND TRIM(r.free_text) != ''
                """, (g["dept"], g["h"])).fetchall()
                entry["distributions"] = dist
                entry["free_texts"] = [f["free_text"] for f in fts]
            out.append(entry)
    return {"k": k, "departments": out}


# ---------- Statisches Frontend ----------

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")
