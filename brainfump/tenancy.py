"""E-4 — Mandantenfähigkeit (Multi-Tenant).

Ein ``BrainFumpKernel`` ist single-tenant: ein Speicherort, ein Gedächtnis.
Der ``TenantManager`` gibt jedem Mandanten einen vollständig isolierten Kernel
(eigenes Verzeichnis → eigene SQLite-Dateien → keine geteilten Tabellen), mit
einem gemeinsamen Verwaltungs-Interface. Isolation entsteht auf DB-Datei-Ebene
(stärker als row-level Tenant-Spalten), Nebenläufigkeit deckt das Locking der
Stores ab.

Backend-Naht: ``kernel_factory`` entkoppelt die Kernel-Erzeugung vom Speicher.
Der Default legt SQLite-Dateien unter ``root/tenants/<id>/`` an; ein
Postgres/Cloud-Backend würde denselben Callback mit einem anderen Kernel-
Konstruktor bedienen (der eigentliche DB-Adapter ist ein separates Ticket —
er lässt sich hier ohne Änderung am Manager einhängen).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Callable

from brainfump.kernel import BrainFumpKernel

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class TenantManager:
    """Verwaltet isolierte Kernel je Mandant."""

    def __init__(
        self,
        root: str | None = None,
        kernel_factory: Callable[[str | None], BrainFumpKernel] | None = None,
    ) -> None:
        self.root = root
        if root is not None:
            self._tenants_dir = os.path.join(root, "tenants")
            os.makedirs(self._tenants_dir, exist_ok=True)
        self._factory = kernel_factory or (lambda path: BrainFumpKernel(path))
        self._cache: dict[str, BrainFumpKernel] = {}

    @staticmethod
    def _validate(tenant_id: str) -> None:
        # Verhindert Path-Traversal und leere/kollidierende IDs.
        if not _TENANT_RE.match(tenant_id or "") or tenant_id in (".", ".."):
            raise ValueError(f"invalid tenant id: {tenant_id!r}")

    def _path_for(self, tenant_id: str) -> str | None:
        return os.path.join(self._tenants_dir, tenant_id) if self.root is not None else None

    def kernel_for(self, tenant_id: str) -> BrainFumpKernel:
        """Isolierten Kernel des Mandanten liefern (gecached)."""
        self._validate(tenant_id)
        if tenant_id not in self._cache:
            self._cache[tenant_id] = self._factory(self._path_for(tenant_id))
        return self._cache[tenant_id]

    def tenants(self) -> list[str]:
        if self.root is not None:
            return sorted(
                d for d in os.listdir(self._tenants_dir)
                if os.path.isdir(os.path.join(self._tenants_dir, d))
            )
        return sorted(self._cache)

    def drop(self, tenant_id: str) -> bool:
        """Mandanten samt Daten entfernen. Gibt True zurück, wenn etwas
        gelöscht wurde."""
        self._validate(tenant_id)
        existed = tenant_id in self._cache
        self._cache.pop(tenant_id, None)
        if self.root is not None:
            path = os.path.join(self._tenants_dir, tenant_id)
            if os.path.isdir(path):
                shutil.rmtree(path)
                return True
        return existed
