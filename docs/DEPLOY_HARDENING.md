# Deploy-Härtung — Agent Execution Layer

Betriebsanforderungen für den Betrieb mit **nicht vollständig
vertrauenswürdigen Tenants** (Sprint 3, Tickets S3-1/S3-2; adressiert die
Deep-Dive-Punkte O1 und O2). Ohne diese Maßnahmen ist die Plattform nur für
interne, vertrauenswürdige Nutzung freigegeben.

## 1. Sandbox-Privilege-Drop (O1)

Der Sandbox-Kindprozess droppt Tool-Code auf `nobody`, **sofern der Container
als root startet**. Damit kann Fremdcode die Plattform-Dateien (z. B.
`/data/billing.db`, die Trace-DB) nicht mehr lesen.

- **Voraussetzung:** Container startet als root (Standard-Dockerfile) und
  behält `CAP_SETUID` + `CAP_SETGID` — sonst schlägt der Drop still fehl und
  Tools laufen mit der Service-UID (Guard meldet `dropped_privileges: false`).
- **Verifikation (Root-Container-Smoke, da CI unprivilegiert läuft):**

  ```bash
  docker compose up -d agent-layer
  # Ein Tool, das versucht, die Billing-DB zu lesen, muss scheitern:
  docker compose exec agent-layer python -c "
  from apps.agent_layer.sandbox import ProcessSandbox
  def probe():
      return open('/data/billing.db','rb').read(16).hex()
  r = ProcessSandbox().run(probe, {})
  print('dropped=', r.dropped_privileges, 'ok=', r.ok, 'err=', r.error)
  "
  # Erwartet: dropped= True  ok= False  err= PermissionError...
  ```

- **Opt-out** pro Tool über `SandboxPolicy(drop_privileges=False)` — nur für
  vertrauenswürdige Plattform-Tools, die die Service-UID brauchen.

## 2. Netz-Isolation (O2)

Tool-Code läuft in-Prozess (Fork) innerhalb des Plattform-Containers. Die
In-Prozess-Socket-Sperre (`allow_network=False`) ist Defense-in-Depth gegen
*versehentlichen* Zugriff; **die harte Grenze zieht das Container-Netz.**

- **Produktion:** Plattform in ein `internal: true`-Netz ohne Internet-Egress;
  vLLM als Service in dasselbe Netz. Fertig als Override:

  ```bash
  docker compose -f docker-compose.yml -f docker-compose.hardened.yml \
    up -d agent-layer vllm
  ```

- **Warum kein Egress im Dev-Default:** Der Standard-Compose lässt das Netz
  offen, damit ein **extern** laufender vLLM (`VLLM_BASE_URL`) erreichbar
  bleibt. Genau dieser externe Zugriff ist im gehärteten Modus untersagt —
  deshalb der Override, der vLLM nach innen holt.
- **Ingress:** Der veröffentlichte API-Port funktioniert auch am internen Netz
  (Ingress-NAT ist unberührt). Für echten Betrieb einen Reverse-Proxy mit TLS
  davorsetzen.

## 3. Container-Härtung (im Standard-Compose aktiv)

Für `agent-layer` bereits in `docker-compose.yml` gesetzt:

| Option | Zweck |
|---|---|
| `cap_drop: [ALL]` + `cap_add: [SETUID, SETGID]` | Nur die Rechte, die der Privilege-Drop braucht — sonst keine |
| `security_opt: [no-new-privileges:true]` | Keine Rechte-Eskalation über setuid-Binaries |
| `read_only: true` | Unveränderliches Root-Filesystem |
| `tmpfs: [/tmp]` | Schreibbarer, nicht-persistenter Platz für Sandbox-Workdirs |
| `PYTHONDONTWRITEBYTECODE=1` | Keine `.pyc`-Schreibversuche aufs read-only FS |
| Volume `/data` | Einziger persistenter, schreibbarer Pfad (SQLite) |

## 4. Betriebs-Checkliste vor externem Go-Live

- [ ] Container startet als root, `CAP_SETUID`/`CAP_SETGID` vorhanden →
      Privilege-Drop greift (`dropped_privileges: true` im Trace der Tool-Calls).
- [ ] `docker-compose.hardened.yml` aktiv → Backend-Netz `internal`, vLLM innen.
- [ ] `ADMIN_TOKEN` gesetzt (kein pro-Instanz-Zufallstoken im Log).
- [ ] Reverse-Proxy mit TLS vor dem API-Port.
- [ ] Budgets/Rate-Limits pro Tenant konfiguriert (siehe Billing-README).
- [ ] Trace-Retention aktiviert (`--retention-days`), falls Aufbewahrung begrenzt
      sein soll.
