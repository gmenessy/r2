"use strict";

const $ = (id) => document.getElementById(id);

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
    body: JSON.stringify(payload),
  });

const money = (usd) => `$${Number(usd || 0).toFixed(6)}`;
const esc = (text) =>
  String(text).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// -- Linke Spalte -------------------------------------------------------------

async function loadScenarios() {
  const { scenarios } = await api("/api/scenarios");
  $("scenario-list").innerHTML = scenarios
    .map(
      (s) => `<button class="scenario" data-name="${s.name}">
        ${esc(s.title)}
        <span class="pillar">${esc(s.pillar)} · Tenant: ${esc(s.tenant)}</span>
        <span class="desc">${esc(s.description)}</span>
      </button>`
    )
    .join("");
  document.querySelectorAll(".scenario").forEach((btn) =>
    btn.addEventListener("click", () => runScenario(btn.dataset.name))
  );
}

async function refreshState() {
  const state = await api("/api/state");
  $("llm-mode").textContent = state.llm.simulated
    ? "LLM: SimulatedLLM (offline testbar)"
    : `LLM: ${state.llm.model}`;
  $("run-list").innerHTML =
    state.recent_runs
      .map(
        (r) => `<div class="runrow" data-run="${r.run_id}">
          <span class="goal">${esc(r.goal)}</span>
          <span class="st">${esc(r.status)} · ${esc(r.tenant)}</span>
        </div>`
      )
      .join("") || '<span class="st">Noch keine Runs.</span>';
  document.querySelectorAll(".runrow").forEach((row) =>
    row.addEventListener("click", () => showRun(row.dataset.run))
  );
  const demo = state.tenants.find((t) => t.tenant === "demo");
  if (demo) {
    $("tenant-spent").innerHTML =
      `Tenant demo: <b>${money(demo.spent_usd)}</b> von ${money(demo.budget_usd)} verbraucht`;
  }
}

// -- Runs ---------------------------------------------------------------------

async function runScenario(name) {
  const result = await post("/api/scenarios/run", { name }).catch(showError);
  if (result) await showRun(result.run_id, result);
}

async function runGoal() {
  const goal = $("goal").value.trim();
  if (!goal) return;
  const result = await post("/api/run", { goal }).catch(showError);
  if (result) await showRun(result.run_id, result);
}

function showError(error) {
  $("placeholder").style.display = "block";
  $("run-view").style.display = "none";
  $("placeholder").textContent = `Fehler: ${error.message}`;
}

const STEP_META = {
  memory_hits: (p) => ({
    cls: "memory", icon: "🧠", label: "Memory",
    detail: p.hits.length
      ? p.hits.map((h) => `(${h.memory_type}) ${h.statement}`).join(" · ")
      : "keine relevanten Erinnerungen",
  }),
  llm_call: (p) => ({
    cls: "llm", icon: "🤖", label: "LLM",
    detail:
      `${p.prompt_tokens}→${p.completion_tokens} Tokens` +
      (p.tool_calls.length ? ` · will Tools: ${p.tool_calls.join(", ")}` : "") +
      (p.content_preview ? ` · „${p.content_preview}"` : ""),
  }),
  budget_stop: (p) => ({
    cls: "budget", icon: "💸", label: "Budget-Stopp", detail: p.reason,
  }),
};

function toolMeta(p) {
  const gate = p.gate || {};
  const sandbox = p.sandbox || {};
  const outcome = p.outcome || {};
  let cls = "tool-ok";
  let verdicts = "";
  if (gate.allowed === false) {
    cls = "tool-block";
    verdicts = '<span class="verdict">🛑 Gatekeeper: block</span>';
  } else if (!outcome.ok) {
    cls = "tool-fail";
    verdicts = `<span class="verdict">⏱ Sandbox: ${esc(sandbox.exit_reason || "error")}</span>`;
  } else if (sandbox.exit_reason) {
    verdicts = `<span class="verdict">🔒 Sandbox: ${esc(sandbox.exit_reason)}</span>`;
  }
  const detail = outcome.ok
    ? JSON.stringify(outcome.value)
    : outcome.error || "";
  return {
    cls, icon: "🛠️", label: `Tool ${p.tool}`,
    detail: `${JSON.stringify(p.args)} → ${detail}`, verdicts,
  };
}

function renderTimeline(trace) {
  $("timeline").innerHTML = trace.steps
    .map((step) => {
      const meta =
        step.kind === "tool_call"
          ? toolMeta(step.payload)
          : (STEP_META[step.kind] || (() => ({ cls: "", icon: "•", label: step.kind, detail: "" })))(
              step.payload
            );
      const lat = step.duration_ms ? `${step.duration_ms.toFixed(0)} ms` : "";
      return `<div class="step ${meta.cls}">
        <span class="icon">${meta.icon}</span>
        <span class="what"><strong>${esc(meta.label)}</strong>${meta.verdicts || ""}
          <div class="detail">${esc(meta.detail)}</div></span>
        <span class="lat">${lat}</span>
      </div>`;
    })
    .join("");
}

async function showRun(runId, result) {
  const [trace, explanation] = await Promise.all([
    api(`/api/trace?run_id=${runId}`),
    api(`/api/explain?run_id=${runId}`),
  ]);
  $("placeholder").style.display = "none";
  $("run-view").style.display = "block";
  $("run-status").textContent = trace.status;
  $("run-status").className = `status ${trace.status}`;
  $("run-cost").textContent = money(explanation.cost.total_usd);
  $("run-id").textContent = runId + (result && result.title ? ` · ${result.title}` : "");
  $("run-answer").textContent = trace.answer || "(keine Antwort)";
  renderTimeline(trace);
  $("narrative").innerHTML = explanation.narrative
    .map((line) => `<li>${esc(line)}</li>`)
    .join("");
  $("cost-breakdown").innerHTML = explanation.cost.items
    .map(
      (item) =>
        `<div><span class="k">${esc(item.kind)} · ${esc(item.detail)}:</span> ${money(item.cost_usd)}</div>`
    )
    .join("");
  await refreshState();
}

$("run-btn").addEventListener("click", runGoal);
loadScenarios().then(refreshState).catch(showError);
