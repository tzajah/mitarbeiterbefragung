"""Bogen-Spezifikation (Fragen) – Teil des Formular-Templates."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .layout import LAYOUT_VERSION, MAX_OPTIONS, MAX_QUESTIONS, compute_layout

# Wert für die explizite "Keine Angabe"-Spalte (KEIN echter Antwortoptionstext)
NO_ANSWER = "Keine Angabe"

DEFAULT_ORG_TITLE = "BETRIEBSRAT"
DEFAULT_ACCENT = "#00965E"
DEFAULT_INTRO = ("Kreuze in jeder Zeile EINE Antwort an. Möchtest du zu einer Frage keine "
                 "Angabe machen, kreuze „Keine Angabe“. Bitte keinen Namen angeben – "
                 "Rückgabe in den Sammelkasten deiner Abteilung.")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Question:
    id: str
    text: str
    kind: str            # "choice" | "scale"
    options: list[str] = field(default_factory=list)   # für choice
    scale_labels: list[str] = field(default_factory=list)  # für scale

    def real_options(self) -> list[str]:
        return self.options if self.kind == "choice" else self.scale_labels

    def answers(self) -> list[str]:
        # feste Extra-Spalte "Keine Angabe" gehört zu JEDER Frage
        return self.real_options() + [NO_ANSWER]


@dataclass
class SurveySpec:
    title: str
    questions: list[Question]
    free_text_prompt: str = "Was soll sich in deiner Abteilung verbessern?"
    org_title: str = DEFAULT_ORG_TITLE
    org_subtitle: str = ""
    accent_color: str = DEFAULT_ACCENT
    intro: str = DEFAULT_INTRO
    layout_version: int = LAYOUT_VERSION

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "free_text_prompt": self.free_text_prompt,
            "org_title": self.org_title,
            "org_subtitle": self.org_subtitle,
            "accent_color": self.accent_color,
            "intro": self.intro,
            "layout_version": self.layout_version,
            "questions": [
                {
                    "id": q.id,
                    "text": q.text,
                    "kind": q.kind,
                    "options": q.options,
                    "scale_labels": q.scale_labels,
                }
                for q in self.questions
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SurveySpec":
        qs = [
            Question(
                id=q["id"],
                text=q["text"],
                kind=q.get("kind", "scale"),
                options=q.get("options", []),
                scale_labels=q.get("scale_labels", []),
            )
            for q in d.get("questions", [])
        ]
        if not (1 <= len(qs) <= MAX_QUESTIONS):
            raise ValueError(f"1–{MAX_QUESTIONS} Fragen erlaubt, got {len(qs)}")
        for q in qs:
            if q.kind not in ("scale", "choice"):
                raise ValueError(f"Frage {q.id}: Typ muss scale oder choice sein")
            if not (2 <= len(q.real_options()) <= MAX_OPTIONS):
                raise ValueError(f"Frage {q.id}: 2–{MAX_OPTIONS} Antwortoptionen")
        spec = cls(
            title=d.get("title", "Mitarbeiterbefragung"),
            questions=qs,
            free_text_prompt=d.get("free_text_prompt", cls("", []).free_text_prompt),
            org_title=d.get("org_title", DEFAULT_ORG_TITLE),
            org_subtitle=d.get("org_subtitle", ""),
            accent_color=d.get("accent_color", DEFAULT_ACCENT),
            intro=d.get("intro", DEFAULT_INTRO),
            # fehlt im Snapshot → Altbestand, gedruckt im alten festen Layout
            layout_version=int(d.get("layout_version", 1)),
        )
        if not HEX_RE.match(spec.accent_color):
            raise ValueError("Akzentfarbe muss im Format #RRGGBB sein")
        compute_layout(spec)  # wirft, wenn der Bogen nicht auf eine Seite passt
        return spec

    def fingerprint(self) -> str:
        """Identität des Fragebogens (Fragen, Antworten, Freitext-Frage) – bewusst ohne
        Design-Felder, damit reine Optik-Änderungen keine neue Version ergeben."""
        canon = {"free_text_prompt": self.free_text_prompt,
                 "questions": [{"id": q.id, "text": q.text, "answers": q.answers()}
                               for q in self.questions]}
        return hashlib.sha256(json.dumps(canon, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SurveySpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# Beispiel-Spec (25 Fragen) für Tests und Demo
def default_spec() -> SurveySpec:
    scale5 = ["stimme nicht zu", "eher nein", "neutral", "eher ja", "stimme zu"]
    topics = [
        ("Arbeitsbelastung", "Meine Arbeitsbelastung ist angemessen."),
        ("Arbeitsbelastung", "Ich kann meine Arbeit in der geplanten Zeit erledigen."),
        ("Arbeitsmittel", "Ich verfüge über die Arbeitsmittel, die ich brauche."),
        ("Arbeitsmittel", "Unsere IT-Systeme funktionieren zuverlässig."),
        ("Zusammenarbeit", "Im Team herrscht eine offene Kommunikationskultur."),
        ("Zusammenarbeit", "Konflikte werden in meinem Team konstruktiv gelöst."),
        ("Zusammenarbeit", "Ich erhalte die Informationen, die ich für meine Arbeit brauche."),
        ("Führung", "Mein Vorgesetzter informiert mich rechtzeitig über Entscheidungen."),
        ("Führung", "Mein Vorgesetzter unterstützt mich bei der beruflichen Entwicklung."),
        ("Führung", "Mein Vorgesetzter würdigt gute Arbeit angemessen."),
        ("Führung", "Ich kann meinem Vorgesetzten offen Probleme schildern."),
        ("Entwicklung", "Ich habe Möglichkeiten, mich beruflich weiterzuentwickeln."),
        ("Entwicklung", "Fortbildungen werden mir ermöglicht."),
        ("Organisation", "Prozesse und Zuständigkeiten sind klar geregelt."),
        ("Organisation", "Sinnvolle Entscheidungen werden zügig umgesetzt."),
        ("Anerkennung", "Meine Arbeit wird angemessen anerkannt."),
        ("Anerkennung", "Gehalts- und Bewertungssysteme empfinde ich als fair."),
        ("Arbeitsumfeld", "Mein Arbeitsplatz ist ergonomisch gut eingerichtet."),
        ("Arbeitsumfeld", "Das Klima in meiner Abteilung ist kollegial."),
        ("Arbeitsumfeld", "Ich kann den Weg zu/zur Arbeit gut bewältigen."),
        ("Engagement", "Ich kann mir vorstellen, langfristig hier zu bleiben."),
        ("Engagement", "Ich empfehle das Unternehmen als Arbeitgeber weiter."),
        ("Engagement", "Ich identifiziere mich mit den Zielen des Unternehmens."),
        ("Bedingungen", "Meine Bezahlung entspricht meiner Leistung."),
        ("Bedingungen", "Die Urlaubs- und Arbeitszeitregelungen passen für mich."),
    ]
    questions = []
    for i, (topic, text) in enumerate(topics, 1):
        questions.append(
            Question(
                id=f"q{i:02d}",
                text=f"{topic}: {text}",
                kind="scale",
                scale_labels=scale5,
            )
        )
    return SurveySpec(
        title="Mitarbeiterbefragung 2026",
        questions=questions,
        free_text_prompt="Was würdest du in deiner Abteilung sofort ändern?",
    )
