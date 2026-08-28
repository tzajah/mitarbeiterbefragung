"""FastAPI-Backend: Stapel drucken, Scans einlesen, Review, k-anonyme Auswertung."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import K_DEFAULT, SCANS_DIR, TEMPLATES_DIR
from .db import get_db, init_db
from .formgen.generator import render_pdf
from .formgen.spec import SurveySpec, default_spec
from .scan.reader import read_scan

app = FastAPI(title="Mitarbeiterbefragung", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Spec-Auflösung (MVP: genau eine aktive Spec) ----------

def _active_spec() -> SurveySpec:
    return default_spec()


# ---------- Stapel ----------

class StackCreate(BaseModel):
    department: str


@app.post("/api/stacks")
def create_stack(body: StackCreate):
    stack_id = uuid.uuid4().hex
    spec = _active_spec()
    pdf = render_pdf(spec, stack_id)
    pdf_path = TEMPLATES_DIR / f"{stack_id}.pdf"
    pdf_path.write_bytes(pdf)
    with get_db() as db:
        db.execute("INSERT INTO stacks (id, department) VALUES (?, ?)",
                   (stack_id, body.department.strip()))
    return {"stack_id": stack_id, "department": body.department,
            "pdf_url": f"/api/stacks/{stack_id}/pdf"}


@app.get("/api/stacks")
def list_stacks():
    with get_db() as db:
        rows = db.execute("""
            SELECT s.id, s.department, s.created_at,
                   (SELECT COUNT(*) FROM responses r
                     WHERE r.stack_id = s.id AND r.finalized = 1) AS n
            FROM stacks s ORDER BY s.created_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/stacks/{stack_id}/pdf")
def stack_pdf(stack_id: str):
    p = TEMPLATES_DIR / f"{stack_id}.pdf"
    if not p.exists():
        raise HTTPException(404, "Stapel-PDF nicht gefunden")
    return FileResponse(p, media_type="application/pdf", filename="befragungsbogen.pdf")


# ---------- Scan-Upload ----------

@app.post("/api/scans")
async def upload_scan(file: UploadFile = File(...)):
    image_bytes = await file.read()
    spec = _active_spec()
    try:
        result = read_scan(image_bytes, spec)
    except ValueError as e:
        raise HTTPException(422, str(e))

    response_id = uuid.uuid4().hex
    stack_id = result.stack_id
    with get_db() as db:
        if stack_id:
            known = db.execute("SELECT 1 FROM stacks WHERE id = ?", (stack_id,)).fetchone()
            if not known:
                stack_id = None  # unbekannter Stapel → manuell zuordnen
        db.execute(
            "INSERT INTO responses (id, stack_id, quad_score) VALUES (?, ?, ?)",
            (response_id, stack_id, result.quad_score))
        for a in result.answers:
            db.execute(
                "INSERT INTO answers (response_id, question_id, value, confidence,"
                " needs_review, note) VALUES (?, ?, ?, ?, ?, ?)",
                (response_id, a.question_id, a.value, a.confidence,
                 int(a.needs_review), a.note))
    (SCANS_DIR / f"{response_id}.jpg").write_bytes(image_bytes)
    return {
        "response_id": response_id,
        "stack_id": stack_id,
        "quad_score": result.quad_score,
        "warnings": result.warnings,
        "answers": [vars(a) for a in result.answers],
        "image_url": f"/api/responses/{response_id}/image",
    }


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
    return {"response": dict(row), "answers": [dict(a) for a in answers],
            "image_url": f"/api/responses/{response_id}/image"}


@app.get("/api/responses/{response_id}/image")
def response_image(response_id: str):
    p = SCANS_DIR / f"{response_id}.jpg"
    if not p.exists():
        raise HTTPException(404, "Scan-Bild nicht gefunden")
    return FileResponse(p, media_type="image/jpeg")


class FinalizeBody(BaseModel):
    stack_id: str | None = None
    free_text: str | None = None
    corrections: dict[str, str | None] = {}  # question_id → finaler Wert


@app.post("/api/responses/{response_id}/finalize")
def finalize_response(response_id: str, body: FinalizeBody):
    with get_db() as db:
        row = db.execute("SELECT finalized FROM responses WHERE id = ?",
                         (response_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Antwort nicht gefunden")
        if not body.stack_id:
            raise HTTPException(400, "Stapel-Zuordnung fehlt (Abteilung)")
        if not db.execute("SELECT 1 FROM stacks WHERE id = ?", (body.stack_id,)).fetchone():
            raise HTTPException(400, "Unbekannter Stapel")
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
    with get_db() as db:
        departments = db.execute("""
            SELECT st.department AS dept, COUNT(*) AS n
            FROM responses r JOIN stacks st ON st.id = r.stack_id
            WHERE r.finalized = 1 GROUP BY st.department
        """).fetchall()
        out = []
        spec = _active_spec()
        for d in departments:
            n = d["n"]
            entry = {"department": d["dept"], "n": n,
                     "suppressed": n < max(k, 1)}
            if not entry["suppressed"]:
                rows = db.execute("""
                    SELECT a.question_id, a.value, COUNT(*) AS c
                    FROM answers a JOIN responses r ON r.id = a.response_id
                    JOIN stacks st ON st.id = r.stack_id
                    WHERE r.finalized = 1 AND st.department = ?
                    GROUP BY a.question_id, a.value
                """, (d["dept"],)).fetchall()
                dist: dict[str, dict[str, int]] = {}
                for r in rows:
                    dist.setdefault(r["question_id"], {})[r["value"] or "(keine Antwort)"] = r["c"]
                fts = db.execute("""
                    SELECT r.free_text FROM responses r
                    JOIN stacks st ON st.id = r.stack_id
                    WHERE r.finalized = 1 AND st.department = ?
                      AND r.free_text IS NOT NULL AND TRIM(r.free_text) != ''
                """, (d["dept"],)).fetchall()
                entry["distributions"] = dist
                entry["free_texts"] = [f["free_text"] for f in fts]
            out.append(entry)
    return {"k": k, "departments": out}


# ---------- Statisches Frontend ----------

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")
