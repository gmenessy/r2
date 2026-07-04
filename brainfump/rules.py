"""Sprint 4 — Runtime Rule Compiler + Runtime Checks.

TRACE-Gedanke: Nutzerkorrekturen werden zu ausführbaren Regeln, nicht nur
zu Erinnerungen. Der Unterschied zwischen Preference Access und Preference
Compliance wird geschlossen, indem Regeln vor Task-Abschluss geprüft werden.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from brainfump._locking import locked
from brainfump.events import Event, utc_now

SEVERITIES = ("info", "warning", "high", "critical")
RULE_STATUSES = frozenset({"active", "revoked", "superseded"})


@dataclass(frozen=True)
class Rule:
    rule_id: str
    condition: dict[str, Any]
    check: dict[str, Any]
    severity: str = "warning"
    message: str = ""
    source: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "condition": self.condition,
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        return cls(**data)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


# Bekannte Abhängigkeiten, die in NL-Korrekturen erkannt werden.
_KNOWN_DEPENDENCIES = ("react", "vue", "angular", "jquery", "svelte", "next")

# "keine React-Abhängigkeit", "no react dependency", "kein Vue", "nicht Angular nutzen"
_FORBID_PATTERN = re.compile(
    r"\b(?:keine?n?|no|not|nicht|ohne)\b[^.]*?\b(" + "|".join(_KNOWN_DEPENDENCIES) + r")\b",
    re.IGNORECASE,
)


class RuleCompiler:
    """Extrahiert aus Nutzerkorrekturen ausführbare Regeln."""

    def compile_correction(self, event: Event) -> Rule | None:
        if event.event_type != "correction":
            return None

        # Strukturierte Korrektur hat Vorrang: payload sagt explizit, was gilt.
        forbidden = list(event.payload.get("forbid_dependencies", ()))
        if not forbidden:
            forbidden = [m.lower() for m in _FORBID_PATTERN.findall(event.content)]
        if not forbidden:
            return None

        condition: dict[str, Any] = {}
        if "project_type" in event.payload:
            condition["project_type"] = event.payload["project_type"]
        if event.case_id is not None:
            condition["case_id"] = event.case_id

        # Deterministische rule_id, damit die Rekompilierung beim Neustart
        # dieselben Regeln erzeugt.
        slug = "_".join(sorted(set(forbidden)))
        return Rule(
            rule_id=f"no_{slug}_{event.event_id[-6:]}",
            condition=condition,
            check={"package_json_must_not_contain": sorted(set(forbidden))},
            severity=event.payload.get("severity", "warning"),
            message=f"Korrektur vom {event.timestamp[:10]}: {event.content}",
            source=event.event_id,
        )


class RuntimeChecker:
    """Prüft Aktionen/Task-Ergebnisse gegen kompilierte Regeln.

    Der Kontext beschreibt den Zustand, der geprüft wird, z. B.:
        {
            "project_type": "mvp_frontend",
            "case_id": "akte_42",
            "package_json": {"dependencies": {"react": "^18.0.0"}},
            "files_modified": ["src/app.js"],
        }
    """

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self.rules: list[Rule] = list(rules or [])

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def check(self, context: dict[str, Any]) -> list[Violation]:
        violations = []
        for rule in self.rules:
            if not self._condition_matches(rule.condition, context):
                continue
            violations.extend(self._run_checks(rule, context))
        return violations

    @staticmethod
    def _condition_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
        return all(context.get(key) == value for key, value in condition.items())

    @staticmethod
    def _run_checks(rule: Rule, context: dict[str, Any]) -> list[Violation]:
        violations = []
        for check_name, check_value in rule.check.items():
            if check_name == "package_json_must_not_contain":
                pkg = context.get("package_json", {})
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                found = sorted(d for d in check_value if d in deps)
                if found:
                    violations.append(
                        Violation(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            message=rule.message or f"verbotene Abhängigkeiten: {found}",
                            details={"forbidden_found": found},
                        )
                    )
            elif check_name == "must_not_contain":
                field_name = check_value["field"]
                values = check_value["values"]
                haystack = str(context.get(field_name, ""))
                found = sorted(v for v in values if v in haystack)
                if found:
                    violations.append(
                        Violation(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            message=rule.message or f"{field_name} enthält: {found}",
                            details={"field": field_name, "forbidden_found": found},
                        )
                    )
            elif check_name == "must_contain":
                field_name = check_value["field"]
                values = check_value["values"]
                haystack = str(context.get(field_name, ""))
                missing = sorted(v for v in values if v not in haystack)
                if missing:
                    violations.append(
                        Violation(
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            message=rule.message or f"{field_name} fehlt: {missing}",
                            details={"field": field_name, "missing": missing},
                        )
                    )
            else:
                raise ValueError(f"unknown check type: {check_name!r}")
        return violations


_RULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    rule_id    TEXT PRIMARY KEY,
    definition TEXT NOT NULL,
    status     TEXT NOT NULL,
    source     TEXT,
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    supersedes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_status ON rules (status);
"""


class RuleStore:
    """E-1 — persistente, versionierte Rules Engine (vfs://rules/).

    Kompilierte Regeln werden dauerhaft gespeichert, statt bei jedem Neustart
    aus correction-Events neu abgeleitet zu werden. Regeln haben einen Status
    (active/revoked/superseded), lassen sich also zurücknehmen und versionieren
    — ein Nutzer, der eine frühere Korrektur widerruft, deaktiviert die Regel,
    ohne das append-only Event Log anzutasten.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_RULE_SCHEMA)

    @locked
    def exists(self, rule_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM rules WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        return row is not None

    @locked
    def add(self, rule: Rule, source: str | None = None, valid_from: str | None = None) -> Rule:
        """Regel als 'active' persistieren. Idempotent (INSERT OR IGNORE):
        eine bereits vorhandene rule_id — auch eine revoked/superseded — wird
        NICHT überschrieben, damit Backfill eine zurückgenommene Regel nicht
        wiederbelebt."""
        self._conn.execute(
            "INSERT OR IGNORE INTO rules VALUES (?, ?, 'active', ?, ?, NULL, NULL, ?)",
            (
                rule.rule_id,
                json.dumps(rule.to_dict()),
                source if source is not None else rule.source,
                valid_from or date.today().isoformat(),
                utc_now(),
            ),
        )
        self._conn.commit()
        return rule

    @locked
    def active(self) -> list[Rule]:
        rows = self._conn.execute(
            "SELECT definition FROM rules WHERE status = 'active' ORDER BY created_at, rule_id"
        )
        return [Rule.from_dict(json.loads(r[0])) for r in rows]

    @locked
    def status_of(self, rule_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM rules WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        return row[0] if row else None

    @locked
    def revoke(self, rule_id: str, valid_to: str | None = None) -> bool:
        cur = self._conn.execute(
            "UPDATE rules SET status = 'revoked', valid_to = ? WHERE rule_id = ? AND status = 'active'",
            (valid_to or date.today().isoformat(), rule_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    @locked
    def supersede(self, old_rule_id: str, new_rule: Rule, source: str | None = None) -> Rule:
        """Alte Regel auf 'superseded' setzen und die neue als Nachfolger
        (mit supersedes-Verweis) aktivieren — versionierte Regel-Historie."""
        today = date.today().isoformat()
        self._conn.execute(
            "UPDATE rules SET status = 'superseded', valid_to = ? WHERE rule_id = ?",
            (today, old_rule_id),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO rules VALUES (?, ?, 'active', ?, ?, NULL, ?, ?)",
            (
                new_rule.rule_id,
                json.dumps(new_rule.to_dict()),
                source if source is not None else new_rule.source,
                today,
                old_rule_id,
                utc_now(),
            ),
        )
        self._conn.commit()
        return new_rule

    @locked
    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
