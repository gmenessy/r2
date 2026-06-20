"""BrainFump NextGen — Agentic Memory Kernel.

Memory ist nicht nur Kontext. Memory ist ein Kontroll- und Lernsystem.

Der Kernel speichert Erinnerungen nicht nur, er:
- versioniert (Evolution Memory)
- verdichtet (Consolidation)
- bewertet (Evaluation)
- übersetzt sie in Regeln (Runtime Rule Compiler)
- warnt oder blockiert Aktionen (Memory Gatekeeper)
"""

from brainfump.events import Event, EventLog, EVENT_TYPES
from brainfump.memory_cards import MemoryCard, MemoryCardStore, MEMORY_TYPES
from brainfump.extractor import MemoryExtractor
from brainfump.evolution import EvolutionMemory, EvolutionPatch, PATCH_TYPES
from brainfump.rules import Rule, RuleCompiler, RuntimeChecker, Violation
from brainfump.gatekeeper import GateDecision, GateFinding, GateMode, MemoryGatekeeper
from brainfump.retrieval import (
    EmbeddingSimilarity,
    LexicalSimilarity,
    Retriever,
    Similarity,
    Weights,
)
from brainfump.consolidation import Consolidator
from brainfump.evaluation import EvaluationHarness, GoldenScenario
from brainfump.kernel import BrainFumpKernel

__version__ = "0.2.0"

__all__ = [
    "BrainFumpKernel",
    "Consolidator",
    "EVENT_TYPES",
    "EmbeddingSimilarity",
    "EvaluationHarness",
    "Event",
    "EventLog",
    "EvolutionMemory",
    "EvolutionPatch",
    "GateDecision",
    "GateFinding",
    "GateMode",
    "GoldenScenario",
    "LexicalSimilarity",
    "MEMORY_TYPES",
    "MemoryCard",
    "MemoryCardStore",
    "MemoryExtractor",
    "MemoryGatekeeper",
    "PATCH_TYPES",
    "Retriever",
    "Rule",
    "RuleCompiler",
    "RuntimeChecker",
    "Similarity",
    "Violation",
    "Weights",
]
