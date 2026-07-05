"""E-3 — Wiki-Projektion.

Eine menschenlesbare Markdown-Projektion des Gedächtnisses pro Akte
(Spec §7: "Wiki dient als menschenlesbare Memory-Projektion, nicht als
alleinige Wahrheit"). Rein abgeleitet und read-only — die Wahrheit bleiben
Event Log, Card Store, Evolution und Graph. Die Projektion bündelt sie zu
einer lesbaren Seite: aktives Wissen nach Typ, das Beziehungsnetz aus dem
Memory Graph (Supersedes/Widersprüche/Abhängigkeiten) und der Verlauf.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from brainfump.kernel import BrainFumpKernel

# Anzeige-Titel je Memory-Typ, in Darstellungsreihenfolge.
_SECTIONS: list[tuple[str, str]] = [
    ("semantic", "Entscheidungen & Wissen"),
    ("preference", "Präferenzen"),
    ("governance", "Governance"),
    ("risk", "Risiken"),
    ("failure", "Gescheiterte Versuche (nicht wiederholen)"),
    ("skill", "Bewährte Vorgehensweisen"),
    ("procedural", "Handlungswissen"),
    ("episodic", "Notierte Ereignisse"),
    ("evolution", "Änderungen"),
]

_REL_LABEL = {
    "supersedes": "ersetzt",
    "exception_to": "Ausnahme zu",
    "contradicts": "widerspricht",
    "depends_on": "hängt ab von",
    "relates_to": "verknüpft mit",
    "evidence": "belegt durch",
}


class WikiProjection:
    """Rendert Markdown-Projektionen aus dem Kernel-Zustand."""

    def __init__(self, kernel: "BrainFumpKernel") -> None:
        self.kernel = kernel

    # -- Index -------------------------------------------------------------------

    def cases(self) -> list[str]:
        return sorted({e.case_id for e in self.kernel.events.query() if e.case_id})

    def render_index(self) -> str:
        lines = ["# Memory-Wiki", "", "_Menschenlesbare Projektion — abgeleitet, nicht die alleinige Wahrheit._", ""]
        cases = self.cases()
        if not cases:
            lines.append("_Noch keine Akten._")
            return "\n".join(lines) + "\n"
        lines.append("## Akten")
        for case in cases:
            n = len(self.kernel.cards.active(case_id=case, include_global=False))
            lines.append(f"- **{case}** — {n} aktive Memory Cards")
        return "\n".join(lines) + "\n"

    # -- Akten-Seite -------------------------------------------------------------

    def render_case(self, case_id: str, max_events: int = 15) -> str:
        cards = self.kernel.cards.active(case_id=case_id, include_global=False)
        events = self.kernel.events.query(case_id=case_id)
        by_id = {c.card_id: c for c in cards}

        lines = [f"# Akte: {case_id}", ""]
        lines.append("_Menschenlesbare Projektion des Gedächtnisses — abgeleitet, nicht die alleinige Wahrheit._")
        lines.append("")

        # Überblick
        edges = [
            e for e in self.kernel.graph.all_edges()
            if e.src in by_id or e.dst in by_id
        ]
        lines.append("## Überblick")
        lines.append(f"- {len(cards)} aktive Memory Cards")
        lines.append(f"- {len(events)} Ereignisse im Verlauf")
        lines.append(f"- {len(edges)} Beziehungen im Memory Graph")
        lines.append("")

        # Wissen nach Typ
        rendered_any = False
        for mem_type, title in _SECTIONS:
            group = [c for c in cards if c.memory_type == mem_type]
            if not group:
                continue
            rendered_any = True
            lines.append(f"## {title}")
            for c in group:
                meta = f"conf {c.confidence:.2f} · trust {c.trust:.2f} · ab {c.valid_from}"
                lines.append(f"- {c.statement}  \n  _{meta}_")
            lines.append("")
        if not rendered_any:
            lines.append("_Noch kein aktives Wissen in dieser Akte._")
            lines.append("")

        # Beziehungen aus dem Graph
        if edges:
            lines.append("## Beziehungen")
            for e in edges:
                label = _REL_LABEL.get(e.rel, e.rel)
                src = self._short(by_id, e.src)
                dst = self._short(by_id, e.dst)
                extra = ""
                if e.rel == "contradicts" and e.meta.get("winner"):
                    extra = f"  (bestätigt: {self._short(by_id, e.meta['winner'])})"
                lines.append(f"- {src} — _{label}_ → {dst}{extra}")
            lines.append("")

        # Verlauf
        if events:
            lines.append(f"## Verlauf (letzte {min(max_events, len(events))})")
            for ev in events[-max_events:][::-1]:
                lines.append(f"- `{ev.timestamp[:19]}` · {ev.event_type} · {ev.source} — {ev.content}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _short(self, by_id: dict, card_id: str) -> str:
        card = by_id.get(card_id) or self.kernel.cards.get(card_id)
        if card is None:
            return f"`{card_id}`"
        text = card.statement
        return text if len(text) <= 60 else text[:57] + "…"
