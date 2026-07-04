"""Trust & Provenance — die Antwort auf das Red-Team-Ergebnis.

Der Kernel schützte bisher vor Versehen, nicht vor Absicht: jede Quelle
durfte globale DNA setzen, Korrekturen zu erzwungenen Regeln machen und per
Widerspruch wahres Wissen löschen. Der ``TrustPolicy`` ordnet jeder Quelle
(``event.source``) ein Vertrauensmaß in ``[0, 1]`` zu und leitet daraus
Berechtigungen ab.

Rückwärtskompatibel: eine frische ``TrustPolicy()`` ist permissiv
(``default=1.0`` → unbekannte Quellen bestehen alle Schwellen), sodass sich
ohne explizite Konfiguration nichts am Verhalten ändert. Restriktion entsteht
erst, wenn man ``default`` senkt und bekannten Quellen gezielt Vertrauen
erteilt.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrustPolicy:
    #: Vertrauen für Quellen ohne expliziten Eintrag.
    default: float = 1.0
    #: Mindestvertrauen, um globale DNA (case_id=None, Governance) zu setzen.
    global_dna_min: float = 0.9
    #: Mindestvertrauen, damit eine Korrektur zu einer erzwungenen Regel wird.
    enforce_rule_min: float = 0.7
    #: Mindestvertrauen, damit eine hinterlegte Fix-Alternative vorgeschlagen wird.
    alternative_min: float = 0.7
    _trust: dict[str, float] = field(default_factory=dict)

    def grant(self, source: str, trust: float) -> "TrustPolicy":
        if not 0.0 <= trust <= 1.0:
            raise ValueError("trust must be within [0, 1]")
        self._trust[source] = trust
        return self

    def trust_of(self, source: str | None) -> float:
        if source is None:
            return self.default
        return self._trust.get(source, self.default)

    def may_write_global_dna(self, source: str | None) -> bool:
        return self.trust_of(source) >= self.global_dna_min

    def may_enforce_rules(self, source: str | None) -> bool:
        return self.trust_of(source) >= self.enforce_rule_min

    def may_suggest_alternative(self, trust: float) -> bool:
        return trust >= self.alternative_min
