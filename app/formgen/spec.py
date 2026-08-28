"""Bogen-Spezifikation (Fragen) – Teil des Formular-Templates."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .layout import MAX_OPTIONS, MAX_QUESTIONS

# Wert für die explizite "Keine Angabe"-Spalte (KEIN echter Antwortoptionstext)
NO_ANSWER = "Keine Angabe"


@dataclass
class Question:
    id: str
    text: str
    kind: str            # "choice" | "scale"
    options: list[str] = field(default_factory=list)   # für choice
    scale_labels: list[str] = field(default_factory=list)  # für scale

    def answers(self) -> list[str]:
        base = self.options if self.kind == "choice" else self.scale_labels
        # feste Extra-Spalte "Keine Angabe" gehört zu JEDER Frage
        return base + [NO_ANSWER]


@dataclass
class SurveySpec:
    title: str
    questions: list[Question]
    free_text_prompt: str = "Was soll sich in deiner Abteilung verbessern?"

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "free_text_prompt": self.free_text_prompt,
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
            if not (2 <= len(q.answers()) <= MAX_OPTIONS):
                raise ValueError(f"Frage {q.id}: 2–{MAX_OPTIONS} Antwortoptionen")
        return cls(
            title=d.get("title", "Mitarbeiterbefragung"),
            questions=qs,
            free_text_prompt=d.get("free_text_prompt", cls("").free_text_prompt),
        )

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
