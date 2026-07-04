"""Semantisches Action-Intent-Matching für Governance-Gates.

Das Ticket aus der Red-Team-Demo: exakte Namen (drop_prod_tables) und Regex
je Karte fangen nur die *anticipierte* Rename-Familie, nicht jedes Synonym
(eliminate_prod_records). Der ehrliche Kompromiss ohne echtes Embedding-Modell
ist eine **geteilte Intent-Ontologie**: Verben und Ressourcen werden EINMAL
zentral als Synonymklassen gepflegt, statt in jeder Governance-Karte neu
aufgezählt. Eine Governance-Karte drückt dann Absicht aus
(``{"verb": "destroy", "resource": "prod"}``) statt eine Zeichenkette.

``IntentMatcher`` ist bewusst so geschnitten, dass ein echtes Embedding-Modell
(oder ein LLM-Klassifikator) dieselbe ``matches(...)``-Rolle übernehmen kann —
die Governance-Semantik im Gatekeeper bleibt unverändert.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")


@runtime_checkable
class ActionMatcher(Protocol):
    """Ordnet einen ``action_type`` einer verbotenen Absicht zu.

    Sowohl der ontologische ``IntentMatcher`` als auch der modellbasierte
    ``EmbeddingIntentMatcher`` erfüllen diese Rolle — der Gatekeeper ist
    gegen die Schnittstelle programmiert, nicht gegen eine Implementierung.
    """

    def matches(self, action_type: str | None, intent: Mapping[str, Any]) -> bool: ...

# Default-Ontologie. Bewusst konservativ und erweiterbar — kein neuronales
# Modell, aber die Synonymklasse lebt an EINER Stelle statt in N Regex.
DEFAULT_VERBS: dict[str, set[str]] = {
    "destroy": {
        "delete", "drop", "purge", "truncate", "wipe", "remove", "destroy",
        "eliminate", "erase", "nuke", "kill", "clear", "flush", "obliterate",
    },
    "exfiltrate": {"export", "dump", "leak", "exfiltrate", "download", "copy", "sync"},
    "escalate": {"grant", "elevate", "escalate", "sudo", "chmod", "chown"},
}

DEFAULT_RESOURCES: dict[str, set[str]] = {
    "prod": {"prod", "production", "prd", "live", "prdn"},
    "secret": {"secret", "secrets", "credential", "credentials", "key", "keys", "token", "vault"},
    "user_data": {"user", "users", "customer", "customers", "pii", "personal"},
}


def _tokens(action_type: str) -> set[str]:
    return set(_TOKEN.findall(action_type.lower()))


class IntentMatcher:
    """Ordnet einen ``action_type`` einer verbotenen Absicht zu.

    ``matches("eliminate_prod_records", {"verb": "destroy", "resource": "prod"})``
    ist ``True``, weil ``eliminate`` in der destroy-Klasse und ``prod`` in der
    prod-Ressourcenklasse liegt — obwohl weder exakter Name noch die frühere
    Regex das Wort ``eliminate`` kannten.
    """

    def __init__(
        self,
        verbs: Mapping[str, set[str]] | None = None,
        resources: Mapping[str, set[str]] | None = None,
    ) -> None:
        self.verbs = dict(verbs) if verbs is not None else {k: set(v) for k, v in DEFAULT_VERBS.items()}
        self.resources = (
            dict(resources) if resources is not None else {k: set(v) for k, v in DEFAULT_RESOURCES.items()}
        )

    def matches(self, action_type: str | None, intent: Mapping[str, Any]) -> bool:
        if not action_type:
            return False
        tokens = _tokens(action_type)

        verb = intent.get("verb")
        if verb is not None:
            verb_class = self.verbs.get(verb, {verb})
            if not (tokens & verb_class):
                return False

        resource = intent.get("resource")
        if resource is not None:
            resource_class = self.resources.get(resource, {resource})
            if not (tokens & resource_class):
                return False

        # Eine leere Absicht darf nicht alles matchen.
        return verb is not None or resource is not None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class EmbeddingIntentMatcher:
    """Modellbasiertes Intent-Matching über einen injizierten Embedding-Provider.

    Erfüllt dieselbe ``matches``-Rolle wie ``IntentMatcher``, entscheidet aber
    per Cosinus-Ähnlichkeit statt Ontologie. Eine Absicht liefert Referenz-
    Beispiele: entweder explizit ``{"like": ["delete production data", …]}``
    oder implizit aus ``{"verb": "destroy", "resource": "prod"}`` (→ "destroy prod").

    WICHTIG (Ehrlichkeit): Die Qualität hängt VOLLSTÄNDIG vom Embedder ab. Ein
    echtes Sprachmodell erkennt ``eliminate`` ~ ``delete``; der abhängigkeitsfreie
    ``HashingEmbedder`` ist rein lexikalisch und tut das NICHT — für ihn bleibt
    die Ontologie (``IntentMatcher``) die bessere Wahl.
    """

    def __init__(self, embed: Callable[[str], Sequence[float]], threshold: float = 0.6) -> None:
        self._embed = embed
        self.threshold = threshold
        self._cache: dict[str, Sequence[float]] = {}

    def _emb(self, text: str) -> Sequence[float]:
        vec = self._cache.get(text)
        if vec is None:
            vec = self._embed(text)
            self._cache[text] = vec
        return vec

    @staticmethod
    def _exemplars(intent: Mapping[str, Any]) -> list[str]:
        like = list(intent.get("like", ()))
        if like:
            return like
        phrase = " ".join(str(intent[k]) for k in ("verb", "resource") if intent.get(k))
        return [phrase] if phrase else []

    def matches(self, action_type: str | None, intent: Mapping[str, Any]) -> bool:
        if not action_type:
            return False
        exemplars = self._exemplars(intent)
        if not exemplars:
            return False
        query = " ".join(_TOKEN.findall(action_type.lower()))
        qv = self._emb(query)
        return any(_cosine(qv, self._emb(ex)) >= self.threshold for ex in exemplars)
