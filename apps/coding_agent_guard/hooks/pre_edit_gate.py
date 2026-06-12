#!/usr/bin/env python3
"""Claude-Code-PreToolUse-Hook: fragt den Coding-Agent-Guard vor jedem Edit.

Installation in .claude/settings.json des Zielprojekts:

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Edit|Write",
            "hooks": [{"type": "command",
                       "command": "python3 /pfad/zu/pre_edit_gate.py"}]
          }
        ]
      }
    }

Umgebung: GUARD_URL (default http://localhost:8020), GUARD_REPO (default:
Verzeichnisname des Projekts). Exit-Code 2 blockiert das Tool; stderr wird
dem Agenten als Feedback gezeigt.
"""

import json
import os
import sys
import urllib.request


def main() -> int:
    hook_input = json.load(sys.stdin)
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    guard_url = os.environ.get("GUARD_URL", "http://localhost:8020")
    repo = os.environ.get("GUARD_REPO") or os.path.basename(
        hook_input.get("cwd", os.getcwd())
    )
    request = urllib.request.Request(
        f"{guard_url}/api/gate",
        data=json.dumps(
            {"repo": repo, "action_type": "edit", "files": [file_path]}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            decision = json.loads(response.read())
    except OSError:
        return 0  # Guard nicht erreichbar → nicht blockieren

    if decision["mode"] == "allow":
        return 0
    for finding in decision["findings"]:
        print(f"[{decision['mode']}] {finding['message']}", file=sys.stderr)
    if decision.get("suggested_alternative"):
        print(f"Alternative: {decision['suggested_alternative']}", file=sys.stderr)
    return 2 if decision["mode"] in ("block", "require_review") else 0


if __name__ == "__main__":
    sys.exit(main())
