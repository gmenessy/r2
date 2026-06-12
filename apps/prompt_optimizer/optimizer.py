"""Prompt-Optimizer mit Langzeitgedächtnis (App Nr. 3).

Jedes Prompt-Experiment wird als Event geloggt und zur Skill- bzw.
Failure-Card verdichtet. Der Memory Gatekeeper verhindert, dass eine
bereits schlecht gescorte Variante erneut getestet wird, und Tonalitäts-
Korrekturen werden zu Runtime-Checks auf den generierten Output
(TRACE: Preference Compliance statt nur Preference Access).

Ein Optimierungs-Projekt entspricht einer Akte (case_id).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from brainfump import BrainFumpKernel
from brainfump.rules import Rule


def prompt_signature(prompt_text: str) -> str:
    """Stabile Signatur einer Prompt-Variante (whitespace-/case-tolerant)."""
    normalized = re.sub(r"\s+", " ", prompt_text.strip().lower())
    return "prompt_" + hashlib.sha256(normalized.encode()).hexdigest()[:16]


class PromptOptimizer:
    def __init__(self, kernel: BrainFumpKernel, success_threshold: float = 0.7) -> None:
        self.kernel = kernel
        self.success_threshold = success_threshold
        # Output-Regeln nach Neustart aus persistierten Korrekturen rekompilieren.
        for event in self.kernel.events.query(event_type="correction"):
            if "output_checks" in event.payload:
                self._compile_output_rules(event)

    # -- Pre-Test Gate ---------------------------------------------------------

    def check_variant(self, project: str, prompt_text: str) -> dict[str, Any]:
        """Vor dem (teuren) Test: Ist diese Variante schon gescheitert?"""
        signature = prompt_signature(prompt_text)
        decision = self.kernel.check_action(
            {
                "action_type": "test_prompt",
                "case_id": project,
                "fix_signature": signature,
            }
        )
        return {
            "signature": signature,
            "decision": decision.to_dict(),
            "known_results": self._known_results(project, signature),
        }

    def _known_results(self, project: str, signature: str) -> list[dict[str, Any]]:
        results = []
        for card in self.kernel.cards.active(case_id=project):
            if card.memory_type not in ("skill", "failure"):
                continue
            if card.payload.get("fix_signature") != signature:
                continue
            results.append(
                {
                    "score": card.payload.get("score"),
                    "outcome": "success" if card.memory_type == "skill" else "failure",
                    "card_id": card.card_id,
                }
            )
        return results

    # -- Experimente -----------------------------------------------------------

    def record_experiment(
        self,
        project: str,
        prompt_text: str,
        score: float,
        task: str = "default",
        notes: str = "",
    ) -> dict[str, Any]:
        if not 0.0 <= score <= 1.0:
            raise ValueError("score must be within [0, 1]")
        signature = prompt_signature(prompt_text)
        succeeded = score >= self.success_threshold

        payload: dict[str, Any] = {
            "fix_signature": signature,
            "prompt_text": prompt_text,
            "task": task,
            "score": score,
        }
        if notes:
            payload["notes"] = notes
        if not succeeded:
            best = self.best_prompt(project, task)
            if best is not None:
                payload["alternative"] = (
                    f"Beste bekannte Variante für '{task}' "
                    f"(Score {best['score']}): {best['prompt_text']}"
                )

        event = self.kernel.record(
            "successful_attempt" if succeeded else "failed_attempt",
            (
                f"Prompt-Variante für '{task}' erreichte Score {score}: "
                f"{prompt_text[:120]}"
            ),
            case_id=project,
            source="prompt_optimizer",
            confidence=score if succeeded else 1.0 - score,
            payload={**payload, "scope": ["prompt", task]},
        )
        return {
            "event_id": event.event_id,
            "signature": signature,
            "outcome": "success" if succeeded else "failure",
            "score": score,
        }

    def best_prompt(self, project: str, task: str) -> dict[str, Any] | None:
        candidates = [
            c.payload
            for c in self.kernel.cards.active(case_id=project, memory_type="skill")
            if c.payload.get("task") == task and "score" in c.payload
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p["score"])

    # -- Korrekturen → Regeln ----------------------------------------------------

    def add_correction(
        self,
        project: str,
        text: str,
        must_contain: list[str] | None = None,
        must_not_contain: list[str] | None = None,
        severity: str = "warning",
    ) -> dict[str, Any]:
        """Tonalitäts-/Stil-Korrektur loggen und als Output-Check kompilieren.

        Die Check-Definition wandert mit ins Event-Payload, damit Regeln
        nach einem Neustart rekompiliert werden können.
        """
        output_checks: dict[str, Any] = {"severity": severity}
        if must_contain:
            output_checks["must_contain"] = list(must_contain)
        if must_not_contain:
            output_checks["must_not_contain"] = list(must_not_contain)
        event = self.kernel.record(
            "correction",
            text,
            case_id=project,
            source="user",
            payload={"scope": ["prompt", "tonality"], "output_checks": output_checks},
        )
        rule_ids = self._compile_output_rules(event)
        return {"event_id": event.event_id, "rule_ids": rule_ids}

    def _compile_output_rules(self, event: Any) -> list[str]:
        checks = event.payload["output_checks"]
        rule_ids = []
        for check_name in ("must_contain", "must_not_contain"):
            values = checks.get(check_name)
            if not values:
                continue
            rule = Rule(
                rule_id=f"{check_name}_{event.event_id[-6:]}",
                condition={"case_id": event.case_id},
                check={check_name: {"field": "output", "values": list(values)}},
                severity=checks.get("severity", "warning"),
                message=f"Korrektur: {event.content}",
                source=event.event_id,
            )
            self.kernel.checker.add_rule(rule)
            rule_ids.append(rule.rule_id)
        return rule_ids

    def validate_output(self, project: str, output: str) -> dict[str, Any]:
        """Generierten Output gegen alle kompilierten Regeln prüfen."""
        decision = self.kernel.check_action(
            {
                "action_type": "validate_output",
                "case_id": project,
                "context": {"output": output},
            }
        )
        return decision.to_dict()

    # -- Memory & Metriken --------------------------------------------------------

    def memory(self, project: str) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.kernel.cards.active(case_id=project)]

    def metrics(self, project: str) -> dict[str, Any]:
        events = self.kernel.events.query(case_id=project)
        experiments = [
            e for e in events if e.event_type in ("successful_attempt", "failed_attempt")
        ]
        scores = [e.payload["score"] for e in experiments if "score" in e.payload]
        gate_events = [
            e
            for e in events
            if e.event_type == "agent_action" and e.source == "gatekeeper"
        ]
        stopped = [
            e
            for e in gate_events
            if e.payload["decision"]["mode"] in ("block", "suggest_alternative")
        ]
        return {
            "experiments": len(experiments),
            "successes": sum(1 for e in experiments if e.event_type == "successful_attempt"),
            "failures": sum(1 for e in experiments if e.event_type == "failed_attempt"),
            "best_score": max(scores) if scores else None,
            "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
            "gate_checks": len(gate_events),
            "blocked_retries": len(stopped),
            "active_rules": len(self.kernel.checker.rules),
            "memory_cards": len(self.kernel.cards.active(case_id=project)),
        }
