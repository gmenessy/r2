"""Prozess-Sandbox für Tool Calling.

Jeder Tool-Aufruf läuft in einem geforkten Kindprozess mit hartem
Ressourcen-Deckel (ToolSandbox/ToolEmu-Prinzip: Tools sind untrusted, der
Host bleibt stabil):

- ``RLIMIT_CPU`` / ``RLIMIT_AS`` / ``RLIMIT_FSIZE`` / ``RLIMIT_NOFILE``
  begrenzen CPU-Sekunden, Adressraum, Dateigröße und offene Deskriptoren.
- Wall-Clock-Timeout: hängende Tools werden terminiert (SIGTERM → SIGKILL).
- Arbeitsverzeichnis ist ein frisches Tempdir; die Prozessumgebung wird auf
  ein Minimum geleert (keine Secrets-Vererbung an Tool-Code).
- Optionale Egress-Sperre: ohne ``allow_network`` wird ``socket.socket`` im
  Kind durch einen werfenden Stub ersetzt (Defense-in-Depth auf
  Bibliotheksebene; harte Netz-Isolation liefert der Container/eine
  Network-Policy darüber).
- Ergebnisse müssen JSON-serialisierbar sein und werden auf
  ``max_output_bytes`` gedeckelt — kein Kanal für unbegrenzte Ausgaben.

Auf Plattformen ohne ``fork`` (z. B. Windows-Entwicklung) degradiert die
Sandbox zu einer Inline-Ausführung ohne Limits und markiert das Ergebnis mit
``hardened=False``; der Ziel-Container (Linux/CPU) läuft immer gehärtet.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

_MIB = 1024 * 1024


@dataclass(frozen=True)
class SandboxPolicy:
    """Ressourcen-Budget eines einzelnen Tool-Aufrufs.

    ``memory_bytes`` ist der zusätzliche Adressraum, den das Tool über den
    per Fork geerbten Parent-Speicher hinaus belegen darf: ``RLIMIT_AS`` misst
    den gesamten Adressraum, ein absolutes Limit würde harmlose Tools killen,
    sobald der Serverprozess selbst groß ist (Deep-Dive-Finding F2).
    """

    wall_timeout_s: float = 10.0
    cpu_seconds: int = 5
    memory_bytes: int = 256 * _MIB
    max_file_bytes: int = 8 * _MIB
    max_open_files: int = 32
    max_output_bytes: int = 64 * 1024
    allow_network: bool = False
    # S3-1/O1: Läuft der Serverprozess als root, droppt das Tool-Kind vor der
    # Ausführung auf einen unprivilegierten Nutzer (nobody) — dann kann
    # Fremdcode die Plattform-Dateien (z. B. /data/billing.db) nicht lesen.
    # Ohne root ist es ein dokumentierter No-op.
    drop_privileges: bool = True


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    value: Any = None
    error: str | None = None
    exit_reason: str = "ok"  # ok | error | timeout | killed | output_limit
    duration_ms: float = 0.0
    hardened: bool = True
    dropped_privileges: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "value": self.value,
            "error": self.error,
            "exit_reason": self.exit_reason,
            "duration_ms": round(self.duration_ms, 2),
            "hardened": self.hardened,
            "dropped_privileges": self.dropped_privileges,
        }


_KEPT_ENV = ("PATH", "LANG", "TZ")


def _inherited_vmsize_bytes() -> int:
    """Aktueller virtueller Adressraum (Linux); 0, wenn nicht bestimmbar."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):  # pragma: no cover - kein procfs
        pass
    return 0  # pragma: no cover - kein procfs


def _would_drop(policy: SandboxPolicy) -> bool:
    """Wird das Kind Privilegien droppen? Nur wenn gewünscht UND als root gestartet."""
    getuid = getattr(os, "getuid", None)
    return policy.drop_privileges and getuid is not None and getuid() == 0


def _drop_to_nobody() -> None:  # pragma: no cover - benötigt root, nicht in CI
    """Auf 'nobody' fallen: Gruppen leeren, dann GID, dann UID (Reihenfolge!)."""
    import pwd

    nobody = pwd.getpwnam("nobody")
    os.setgroups([])
    os.setgid(nobody.pw_gid)
    os.setuid(nobody.pw_uid)


def _harden_child(policy: SandboxPolicy, workdir: str) -> bool:  # pragma: no cover - läuft im Kind
    import resource
    import socket

    resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds + 1))
    # Limit relativ zum geerbten Adressraum: memory_bytes = Spielraum des Tools.
    memory_cap = _inherited_vmsize_bytes() + policy.memory_bytes
    resource.setrlimit(resource.RLIMIT_AS, (memory_cap, memory_cap))
    resource.setrlimit(resource.RLIMIT_FSIZE, (policy.max_file_bytes, policy.max_file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (policy.max_open_files, policy.max_open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    # Vor einem eventuellen UID-Drop das eigene Tempdir für den Zielnutzer
    # beschreibbar machen (es gehört noch dem Servicenutzer, 0700).
    will_drop = _would_drop(policy)
    if will_drop:
        os.chmod(workdir, 0o777)
    os.chdir(workdir)

    kept = {k: os.environ[k] for k in _KEPT_ENV if k in os.environ}
    os.environ.clear()
    os.environ.update(kept)

    if not policy.allow_network:
        def _blocked(*_args: Any, **_kwargs: Any) -> Any:
            raise PermissionError("network egress is disabled by sandbox policy")

        socket.socket = _blocked  # type: ignore[misc,assignment]
        socket.create_connection = _blocked  # type: ignore[assignment]

    # UID-Drop als LETZTES: danach sind keine privilegierten Schritte mehr
    # möglich, und Fremdcode läuft ohne Zugriff auf Plattform-Dateien.
    if will_drop:
        _drop_to_nobody()
    return will_drop


def _child_main(
    conn: Any,
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    policy: SandboxPolicy,
    workdir: str,
) -> None:  # pragma: no cover - läuft im Kindprozess, nicht im Coverage-Prozess
    dropped = False
    try:
        dropped = _harden_child(policy, workdir)
        value = fn(**kwargs)
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded.encode()) > policy.max_output_bytes:
            conn.send({"ok": False, "error": f"tool output exceeds {policy.max_output_bytes} bytes",
                       "exit_reason": "output_limit", "dropped": dropped})
        else:
            conn.send({"ok": True, "value": json.loads(encoded), "exit_reason": "ok",
                       "dropped": dropped})
    except MemoryError:
        conn.send({"ok": False, "error": "memory limit exceeded", "exit_reason": "killed",
                   "dropped": dropped})
    except BaseException as exc:  # noqa: BLE001 - Grenze der Sandbox, alles melden
        conn.send({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "exit_reason": "error",
            "trace": traceback.format_exc(limit=5),
            "dropped": dropped,
        })
    finally:
        conn.close()


class ProcessSandbox:
    """Führt Callables prozess-isoliert unter einer :class:`SandboxPolicy` aus."""

    def __init__(self) -> None:
        try:
            self._ctx: multiprocessing.context.BaseContext | None = multiprocessing.get_context("fork")
        except ValueError:  # pragma: no cover - nur auf Nicht-POSIX
            self._ctx = None

    @property
    def hardened(self) -> bool:
        return self._ctx is not None

    def run(
        self, fn: Callable[..., Any], kwargs: dict[str, Any], policy: SandboxPolicy | None = None
    ) -> SandboxResult:
        policy = policy or SandboxPolicy()
        start = time.perf_counter()
        if self._ctx is None:  # pragma: no cover - Fallback für Nicht-POSIX
            return self._run_inline(fn, kwargs, start)

        with tempfile.TemporaryDirectory(prefix="agent-tool-") as workdir:
            parent_conn, child_conn = self._ctx.Pipe(duplex=False)
            process = self._ctx.Process(
                target=_child_main, args=(child_conn, fn, kwargs, policy, workdir), daemon=True
            )
            process.start()
            child_conn.close()
            payload = self._collect(parent_conn, process, policy.wall_timeout_s)
            parent_conn.close()

        duration_ms = (time.perf_counter() - start) * 1000
        return SandboxResult(
            ok=payload.get("ok", False),
            value=payload.get("value"),
            error=payload.get("error"),
            exit_reason=payload.get("exit_reason", "error"),
            duration_ms=duration_ms,
            dropped_privileges=payload.get("dropped", False),
        )

    @staticmethod
    def _collect(parent_conn: Any, process: Any, timeout_s: float) -> dict[str, Any]:
        if parent_conn.poll(timeout_s):
            try:
                payload = parent_conn.recv()
            except EOFError:
                payload = {"ok": False, "error": "tool process died before reporting",
                           "exit_reason": "killed"}
        else:
            payload = {"ok": False, "error": f"wall timeout after {timeout_s}s",
                       "exit_reason": "timeout"}
        process.join(0.5)
        if process.is_alive():
            process.terminate()
            process.join(0.5)
        if process.is_alive():  # pragma: no cover - SIGTERM reicht praktisch immer
            process.kill()
            process.join(0.5)
        # Kind ohne Report (z. B. SIGKILL durch Kernel-OOM oder RLIMIT_CPU).
        if payload.get("exit_reason") == "ok" or payload.get("ok"):
            return payload
        if payload.get("error") is None:
            payload = {"ok": False, "error": f"tool process exited with {process.exitcode}",
                       "exit_reason": "killed"}
        return payload

    @staticmethod
    def _run_inline(
        fn: Callable[..., Any], kwargs: dict[str, Any], start: float
    ) -> SandboxResult:  # pragma: no cover - Fallback für Nicht-POSIX
        try:
            value = json.loads(json.dumps(fn(**kwargs), ensure_ascii=False, default=str))
            return SandboxResult(ok=True, value=value, hardened=False,
                                 duration_ms=(time.perf_counter() - start) * 1000)
        except Exception as exc:  # noqa: BLE001
            return SandboxResult(ok=False, error=f"{type(exc).__name__}: {exc}",
                                 exit_reason="error", hardened=False,
                                 duration_ms=(time.perf_counter() - start) * 1000)


# Bequemer Default für Registry/Runtime.
DEFAULT_POLICY = SandboxPolicy()
