#!/usr/bin/env python3
"""Chronos — Zeitreise durchs Projektgedächtnis.

Treibt Evolution Memory und den Validity Resolver an die Grenze:
"Was wusste das System am Stichtag X?" Wir bauen die Wissens-Historie eines
Projekts über ein Jahr auf (Entscheidungen, Ersetzungen, Scope-Ausnahmen,
Deprecations) und reisen dann zu verschiedenen Daten zurück.

Am Ende: bewusst die GRENZEN des Zeitmodells vorführen.

    python3 demos/chronos.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from brainfump.evolution import EvolutionPatch  # noqa: E402
from demos._console import cyan, dim, green, note, red, result, step, title  # noqa: E402

CASE = "projekt_aurora"


def seed_history(kernel: BrainFumpKernel) -> str:
    """Saubere Evolution: Vanilla → (admin:Vue-Ausnahme) → Svelte. Liefert die
    card_id der ursprünglichen Frontend-Regel (Kopf der Patch-Kette)."""
    # T0 — Gründungsentscheidung (2026-01-15): Vanilla JS.
    kernel.record(
        "decision", "Frontend-Framework: Vanilla JS (kein Build-Step).",
        case_id=CASE, timestamp="2026-01-15T09:00:00Z",
        payload={"scope": ["frontend"], "topic": "framework"},
    )
    card = kernel.cards.active(case_id=CASE, include_global=False)[-1]

    # T1 — Scope-Ausnahme (2026-04-01): Admin-Backend darf Vue.
    kernel.patch(EvolutionPatch(
        target_memory=card.card_id, patch_type="scope_exception",
        new_value="Vue erlaubt für das Admin-Backend.",
        scope=("admin_backend",), valid_from="2026-04-01",
    ))

    # T2 — Ersetzung (2026-07-01): Migration auf Svelte projektweit.
    kernel.patch(EvolutionPatch(
        target_memory=card.card_id, patch_type="replace",
        old_value="Vanilla JS", new_value="Frontend-Framework: Svelte (projektweit).",
        valid_from="2026-07-01",
    ))
    return card.card_id


def _show(kernel: BrainFumpKernel, card_id: str, d: str) -> None:
    front = kernel.evolution.resolve(card_id, on_date=d, scope="frontend")
    admin = kernel.evolution.resolve(card_id, on_date=d, scope="admin_backend")
    print(f"   {cyan('frontend')}      → {front.statement if front else red('— nichts Gültiges —')}")
    print(f"   {cyan('admin_backend')} → {admin.statement if admin else red('— nichts Gültiges —')}")


def timeline_snapshots(kernel: BrainFumpKernel, card_id: str) -> None:
    title("CHRONOS · Wissens-Historie einer Frontend-Regel")
    for d, was in [
        ("2026-02-01", "Anfang: nur Vanilla"),
        ("2026-05-01", "nach Scope-Ausnahme fürs Admin-Backend"),
        ("2026-08-01", "nach Migration auf Svelte"),
    ]:
        step(f"Stichtag {d}", dim(f"({was})"))
        _show(kernel, card_id, d)
    print(f"\n   {green('Beobachtung:')} Die admin_backend-Ausnahme überdauert die replace-Migration —")
    print(f"   {dim('sie zeigt via exception_to auf die Kette, nicht auf eine einzelne Version.')}")


def push_limits(kernel: BrainFumpKernel, card_id: str) -> None:
    title("CHRONOS · Grenzen des Zeitmodells (Q4: das Team mustert Svelte aus)")

    svelte = kernel.evolution.resolve(card_id, on_date="2026-09-01", scope="frontend")
    step("Aktion", f"deprecate der Svelte-Karte per 2026-10-01  {dim('(' + svelte.card_id + ')')}")
    kernel.patch(EvolutionPatch(
        target_memory=svelte.card_id, patch_type="deprecate", valid_from="2026-10-01",
    ))

    # Grenze 1: deprecate archiviert die Karte VOLLSTÄNDIG — auch historische
    # Stichtage VOR der Deprecation verlieren diesen Stand.
    step("Grenze 1", "Rückblick auf 2026-08-01 (als Svelte unstrittig galt):")
    back = kernel.evolution.resolve(card_id, on_date="2026-08-01", scope="frontend")
    result(
        back is not None and "Svelte" in (back.statement if back else ""),
        f"frontend am 2026-08-01 → {back.statement if back else red('None')}",
    )
    note("deprecate setzt status=archived; resolve filtert archivierte Stände IMMER heraus —")
    note("auch für Daten, an denen die Version nachweislich gültig war. Die Historie 'vergisst' rückwirkend.")

    # Grenze 2: Die admin_backend-Ausnahme lebt als AKTIVE Karte weiter, ist aber
    # über resolve() nicht mehr erreichbar, weil die Basis-Kette tot ist.
    step("Grenze 2", "Die Vue-Ausnahme fürs Admin-Backend ist noch eine AKTIVE Karte:")
    exc_alive = any(
        c.payload.get("exception_to") for c in kernel.cards.active(case_id=CASE, include_global=False)
    )
    admin = kernel.evolution.resolve(card_id, on_date="2026-08-01", scope="admin_backend")
    result(
        admin is not None,
        f"Karte aktiv im Store: {green('ja') if exc_alive else red('nein')} — "
        f"aber resolve(admin_backend, 2026-08-01) → {red('None') if admin is None else admin.statement}",
    )
    note("Verwaiste Ausnahme: resolve() gibt None zurück, sobald die Basis-Kette None ist —")
    note("die noch aktive Ausnahme wird nie erreicht. Aktiver Store-Stand ≠ auflösbarer Stand.")

    # Grenze 3: Der Gatekeeper kennt nur 'heute'.
    step("Grenze 3", "Der Gatekeeper prüft nur gegen den HEUTIGEN Stand, nicht gegen ein Datum:")
    result(
        False,
        "check_action() hat keinen on_date-Parameter — 'wäre diese Aktion am "
        "2026-05-01 blockiert worden?' ist nicht beantwortbar.",
    )
    note("Für Audit/Forensik fehlt eine Zeitachse im Gate (der Store hätte sie via on_date).")


def main() -> None:
    kernel = BrainFumpKernel()
    card_id = seed_history(kernel)
    timeline_snapshots(kernel, card_id)
    push_limits(kernel, card_id)
    print(f"\n{dim('Chronos fertig. Evolution Memory trägt die Historie sauber durch replace/scope_exception —')}")
    print(f"{dim('aber deprecate ist ein hartes, rückwirkendes Vergessen und kann Ausnahmen verwaisen lassen.')}\n")


if __name__ == "__main__":
    main()
