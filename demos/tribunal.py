#!/usr/bin/env python3
"""Tribunal — wenn drei Agenten sich widersprechen.

Drei Agenten (Rechercheur, Architekt, Security) schreiben Fakten über
dasselbe Projekt ins Case-Memory. Manche widersprechen sich. Wir lassen die
Consolidation urteilen und zeigen schonungslos, WELCHE Widersprüche die
(bewusst simple, abhängigkeitsfreie) Erkennung fängt — und welche nicht.

    python3 demos/tribunal.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from demos._console import dim, note, red, result, step, title, yellow  # noqa: E402

CASE = "projekt_hydra"


def _decide(kernel: BrainFumpKernel, agent: str, statement: str, scope, **payload) -> str:
    ev = kernel.record(
        "decision", statement, case_id=CASE, source=agent,
        payload={"scope": list(scope), **payload},
    )
    return ev.event_id


def seed_debate(kernel: BrainFumpKernel) -> None:
    title("TRIBUNAL · Drei Agenten, ein Case-Memory")
    step("Rechercheur", "trägt Basisfakten ein")
    _decide(kernel, "rechercheur", "Die API nutzt OAuth2 zur Authentifizierung.",
            ("auth",))
    _decide(kernel, "rechercheur", "Die Datenbank ist PostgreSQL.",
            ("db",), engine="postgres")
    _decide(kernel, "rechercheur", "Deployments erfolgen täglich per CI.",
            ("deploy",))

    step("Architekt", "widerspricht teils direkt, teils inhaltlich")
    # (A) Direkter Negations-Widerspruch, hohe Token-Überlappung.
    _decide(kernel, "architekt", "Die API nutzt kein OAuth2 zur Authentifizierung.",
            ("auth",))
    # (B) Sachwiderspruch OHNE Negation, andere Tokens (postgres vs mysql).
    _decide(kernel, "architekt", "Die Datenbank ist MySQL.",
            ("db",), engine="mysql")

    step("Security", "umschreibt einen Widerspruch elegant")
    # (C) Inhaltlicher Widerspruch zu 'täglich', aber ganz andere Worte.
    _decide(kernel, "security", "Produktions-Deployments sind auf ein Release pro Quartal begrenzt.",
            ("deploy",))


def run_tribunal(kernel: BrainFumpKernel) -> None:
    title("TRIBUNAL · Das Urteil der Consolidation")

    before = kernel.cards.active(case_id=CASE, include_global=False)
    step("Vor dem Urteil", f"{len(before)} aktive Fakten im Case-Memory")

    # Konflikte über gemeinsame payload-Keys (strukturiert).
    conflicts = kernel.evolution.detect_conflicts(case_id=CASE)
    step("detect_conflicts", "(strukturiert: gleicher payload-Key, anderer Wert)")
    if conflicts:
        for a, b in conflicts:
            print(f"   {yellow('⚑')} {a.statement}  {dim('⇄')}  {b.statement}")
        result(True, f"{len(conflicts)} strukturierter Konflikt erkannt (postgres ⇄ mysql via payload).")
    else:
        result(False, "kein struktureller Konflikt erkannt.")

    # Textuelle Widerspruchserkennung (Negation + Token-Jaccard >= 0.8).
    report = kernel.consolidate(case_id=CASE)
    step("consolidate", "(textuell: Negation + Token-Überlappung ≥ 0,8)")
    ids = {c.card_id: c for c in before}
    for a_id, b_id in report.contradictions:
        print(f"   {red('⚔')} {ids[a_id].statement}")
        print(f"      {dim('vs')} {ids[b_id].statement}")
    result(
        len(report.contradictions) >= 1,
        f"{len(report.contradictions)} textueller Widerspruch erkannt (OAuth2 ⇔ kein OAuth2).",
    )


def push_limits(kernel: BrainFumpKernel) -> None:
    title("TRIBUNAL · Grenzen der Widerspruchsauflösung")

    # Grenze 1: Sachwiderspruch ohne Negation, andere Tokens → NICHT erkannt.
    step("Grenze 1", "Postgres vs. MySQL wurde nur STRUKTURELL (payload) gefunden, nicht textuell:")
    result(
        False,
        "'Die Datenbank ist PostgreSQL' vs 'Die Datenbank ist MySQL' teilt keine "
        "Negation — _contradicts sieht keinen Widerspruch. Nur der gesetzte "
        "payload-Key 'engine' rettet die Erkennung.",
    )
    note("Ohne strukturiertes payload wäre dieser Sachwiderspruch komplett unsichtbar.")

    # Grenze 2: Umschriebener Widerspruch (täglich vs. quartalsweise) → gar nicht erkannt.
    step("Grenze 2", "'täglich' vs. 'ein Release pro Quartal' bleibt komplett unentdeckt:")
    still_active = [
        c.statement for c in kernel.cards.active(case_id=CASE, include_global=False)
        if "deploy" in c.scope
    ]
    result(
        False,
        f"beide Deploy-Aussagen sind weiter aktiv ({len(still_active)}) — keine Negation, "
        "kaum Token-Überlappung, kein gemeinsamer payload-Key.",
    )
    for s in still_active:
        note(s)

    # Grenze 3: Auflösung = BEIDE stilllegen, kein Mensch-im-Loop.
    step("Grenze 3", "Ein erkannter Widerspruch legt BEIDE Karten still (status=contradicted):")
    auth_active = [
        c for c in kernel.cards.active(case_id=CASE, include_global=False) if "auth" in c.scope
    ]
    result(
        False,  # das korrekt bewiesene Verhalten IST hier die Schwäche
        f"nach dem Urteil sind {red(str(len(auth_active)))} "
        "aktive Auth-Fakten übrig — die WAHRE Aussage verschwindet mit der falschen.",
    )
    note("Consolidation kennt kein 'require_review': Retrieval & Gatekeeper sehen danach GAR NICHTS")
    note("mehr zum Thema Auth — ein stiller Wissensverlust statt einer Eskalation an den Menschen.")


def main() -> None:
    kernel = BrainFumpKernel()
    seed_debate(kernel)
    run_tribunal(kernel)
    push_limits(kernel)
    print(f"\n{dim('Tribunal fertig. Die Erkennung ist ehrlich billig: Negation + Token-Jaccard fangen die')}")
    print(f"{dim('plumpen Fälle, strukturierte payloads die sauberen — semantische Widersprüche brauchen ein Modell.')}\n")


if __name__ == "__main__":
    main()
