# Prompt-Optimizer mit Langzeitgedächtnis

App Nr. 3 der BrainFump-Suite: Ein Prompt-Testlabor, das sich an jedes
Experiment erinnert — und Konsequenzen daraus zieht.

## Was es kann

- **Experimente aufzeichnen**: Prompt + Task + Score → Event Log →
  Skill Card (Score ≥ 0.7) oder Failure Card (Score < 0.7).
- **Gescheiterte Varianten blockieren**: Der Memory Gatekeeper erkennt
  eine bereits schlecht gescorte Variante an ihrer Signatur
  (whitespace-/case-tolerant) und blockiert den Retry — oder schlägt die
  beste bekannte Variante für denselben Task als Alternative vor.
- **Korrekturen ausführbar machen** (TRACE): „Antworten im Du-Stil, nie
  siezen" wird zur Regel `must_not_contain: ["Sehr geehrte"]` auf dem
  generierten Output — geprüft vor Task-Abschluss, nicht nur gespeichert.
- **Projekt-Isolation**: Jedes Optimierungs-Projekt ist eine Akte
  (case_id); Memories und Regeln leaken nicht zwischen Projekten.
- **Metriken**: Experimente, Scores, blockierte Retries, aktive Regeln.

## Start

```bash
# über Docker Compose (Repo-Wurzel)
docker compose up prompt-optimizer
# → http://localhost:8030

# oder lokal
python3 apps/prompt_optimizer/server.py --port 8030 --data ./data
```

## API

| Route | Zweck |
|---|---|
| `POST /api/check` | Pre-Test Gate: `{project, prompt_text}` |
| `POST /api/experiments` | `{project, prompt_text, task, score}` |
| `POST /api/corrections` | `{project, text, must_contain?, must_not_contain?}` |
| `POST /api/outputs/validate` | `{project, output}` → Gate-Entscheidung |
| `GET /api/memory?project=` | aktive Memory Cards |
| `GET /api/metrics?project=` | Optimierungs-/Memory-Metriken |
