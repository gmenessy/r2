"""Kleine ANSI-Konsolen-Helfer für die Demos (keine Abhängigkeiten)."""

from __future__ import annotations

import os
import sys

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def red(t: str) -> str:
    return _c("31", t)


def yellow(t: str) -> str:
    return _c("33", t)


def cyan(t: str) -> str:
    return _c("36", t)


def magenta(t: str) -> str:
    return _c("35", t)


def title(text: str) -> None:
    line = "═" * min(len(text) + 2, 72)
    print(f"\n{cyan(line)}\n {bold(text)}\n{cyan(line)}")


def step(label: str, text: str = "") -> None:
    print(f"{magenta('▸')} {bold(label)}" + (f"  {text}" if text else ""))


def result(ok: bool, text: str) -> None:
    mark = green("✔ HÄLT") if ok else red("✘ GRENZE")
    print(f"   {mark}  {text}")


def note(text: str) -> None:
    print(f"   {dim('· ' + text)}")
