"""Sprint 4 — Runtime Rule Compiler + Runtime Checks.

TRACE-Gedanke: Nutzerkorrekturen werden zu ausführbaren Regeln, nicht nur
zu Erinnerungen. Der Unterschied zwischen Preference Access und Preference
Compliance wird geschlossen, indem Regeln vor Task-Abschluss geprüft werden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from brainfump.events import Event

SEVERITIES = ("info", "warning", "high", "critical")


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
