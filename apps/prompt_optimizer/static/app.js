"use strict";

const $ = (id) => document.getElementById(id);
const project = () => $("project").value.trim() || "default";

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

const post = (path, payload) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: project(), ...payload }),
  });

function renderGate(el, decision, extra) {
  el.className = "gate " + decision.mode;
  const findings = decision.findings
    .map((f) => `<li><strong>${f.gate}</strong> (${f.severity}): ${f.message}</li>`)
    .join("");
  el.innerHTML =
    `<span class="mode">${decision.mode.replace("_", " ")}</span>` +
    (findings ? `<ul>${findings}</ul>` : " — keine Einwände.") +
    (decision.suggested_alternative
      ? `<p>💡 ${decision.suggested_alternative}</p>`
      : "") +
    (extra || "");
}

async function refresh() {
  const metrics = await api(`/api/metrics?project=${encodeURIComponent(project())}`);
  $("metrics").innerHTML = Object.entries(metrics)
    .map(
      ([name, value]) =>
        `<div class="metric"><div class="value">${value ?? "–"}</div><div class="name">${name}</div></div>`
    )
    .join("");

  const { cards } = await api(`/api/memory?project=${encodeURIComponent(project())}`);
  $("memory-list").innerHTML =
    cards
      .slice()
      .reverse()
      .map(
        (card) =>
          `<div class="card ${card.memory_type}">` +
          `<span class="type">${card.memory_type} · conf ${card.confidence.toFixed(2)} · ab ${card.valid_from}</span>` +
          `<div>${card.statement}</div></div>`
      )
      .join("") || '<span style="color: var(--muted)">Noch keine Erinnerungen.</span>';
}

$("btn-check").addEventListener("click", async () => {
  try {
    const result = await post("/api/check", { prompt_text: $("prompt-text").value });
    const known = result.known_results
      .map((r) => `<li>bereits getestet: Score ${r.score} (${r.outcome})</li>`)
      .join("");
    renderGate($("gate-card"), result.decision, known ? `<ul>${known}</ul>` : "");
  } catch (error) {
    alert(error.message);
  }
});

$("btn-record").addEventListener("click", async () => {
  try {
    const result = await post("/api/experiments", {
      prompt_text: $("prompt-text").value,
      task: $("task").value.trim() || "default",
      score: parseFloat($("score").value),
    });
    renderGate(
      $("gate-card"),
      { mode: result.outcome === "success" ? "allow" : "warn", findings: [] },
      `<p>Experiment gespeichert: <strong>${result.outcome}</strong> (Score ${result.score})</p>`
    );
    await refresh();
  } catch (error) {
    alert(error.message);
  }
});

$("btn-correction").addEventListener("click", async () => {
  const split = (value) =>
    value.split(",").map((s) => s.trim()).filter(Boolean);
  try {
    const result = await post("/api/corrections", {
      text: $("correction-text").value,
      must_contain: split($("must-contain").value),
      must_not_contain: split($("must-not-contain").value),
    });
    alert(`Regel(n) kompiliert: ${result.rule_ids.join(", ") || "keine (nur Memory Card)"}`);
    await refresh();
  } catch (error) {
    alert(error.message);
  }
});

$("btn-validate").addEventListener("click", async () => {
  try {
    const decision = await post("/api/outputs/validate", { output: $("output-text").value });
    renderGate($("validate-card"), decision);
    await refresh();
  } catch (error) {
    alert(error.message);
  }
});

$("project").addEventListener("change", refresh);
refresh();
