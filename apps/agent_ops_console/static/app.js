"use strict";

const $ = (id) => document.getElementById(id);

async function api(path) {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

async function postRaw(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return { status: response.status, body: await response.json() };
}

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
        <span class="pillar">${esc(s.pillar)}</span>
        <span class="desc">${esc(s.description)}</span>
        ${s.async ? '<span class="flag">async</span>' : ""}
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
    ? `SimulatedLLM (offline) · Rate-Limit ${state.rate_limit.per_minute}/min, Burst ${state.rate_limit.burst}`
    : `LLM: ${state.llm.model}`;
  $("run-list").innerHTML =
    state.recent_runs
      .map(
        (r) => `<div class="runrow" data-run="${r.run_id}">
          <span class="goal">${esc(r.goal)}</span>
          <span class="st">${esc(r.status)}</span>
        </div>`
      )
      .join("") || '<span class="st">Noch keine Runs.</span>';
  document.querySelectorAll(".runrow").forEach((row) =>
    row.addEventListener("click", () => watchRun(row.dataset.run))
  );
  const t = state.tenant;
  $("tenant-spent").innerHTML =
    `Tenant ops: <b>${money(t.spent_usd)}</b> von ${money(t.budget_usd)} verbraucht`;
}

// -- Runs & Live-Feed -----------------------------------------------------------

let activeStream = null;

async function runScenario(name) {
  const { status, body } = await postRaw("/api/scenarios/run", { name });
  if (status === 429 || body.status === "rate_limited") {
    showRateBanner(body);
    return;
  }
  hideRateBanner();
  if (body.run_id) watchRun(body.run_id, body);
  await refreshState();
}

async function fireNoisyNeighborX3() {
  for (let i = 0; i < 3; i++) {
    await runScenario("noisy_neighbor");
  }
}

function showRateBanner(body) {
  const el = $("rate-banner");
  el.style.display = "block";
  el.textContent =
    `🐝 429 Rate-Limit — Token-Bucket leer. Retry-After: ${body.retry_after_s}s ` +
    `(Szenario: ${body.title || "noisy_neighbor"})`;
}
function hideRateBanner() {
  $("rate-banner").style.display = "none";
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
      (p.tool_calls.length ? ` · will Tools: ${p.tool_calls.join(", ")}` : ""),
  }),
  budget_stop: (p) => ({ cls: "tool-fail", icon: "💸", label: "Budget-Stopp", detail: p.reason }),
  llm_error: (p) => ({ cls: "tool-fail", icon: "⚠️", label: "LLM-Fehler", detail: p.error }),
};

function toolLine(p) {
  const gate = p.gate || {};
  const outcome = p.outcome || {};
  const sandbox = p.sandbox || {};
  let cls = "tool-ok";
  let verdict = "";
  if (gate.allowed === false) {
    cls = "tool-block";
    verdict = '<span class="verdict">🛑 Gatekeeper: block</span>';
  } else if (!outcome.ok) {
    cls = "tool-fail";
    verdict = `<span class="verdict">⏱ ${esc(sandbox.exit_reason || "error")}</span>`;
  }
  const detail = outcome.ok ? JSON.stringify(outcome.value) : outcome.error || "";
  return { cls, icon: "🛠️", label: `Tool ${p.tool}`, verdict, detail: `${JSON.stringify(p.args)} → ${detail}` };
}

function appendFeedLine(step) {
  const feed = $("feed");
  const meta =
    step.kind === "tool_call"
      ? toolLine(step.payload)
      : (STEP_META[step.kind] || (() => ({ cls: "", icon: "•", label: step.kind, detail: "" })))(step.payload);
  const lat = step.duration_ms ? `${step.duration_ms.toFixed(0)} ms` : "";
  const div = document.createElement("div");
  div.className = `line ${meta.cls}`;
  div.innerHTML =
    `<span class="icon">${meta.icon}</span>` +
    `<span><strong>${esc(meta.label)}</strong>${meta.verdict || ""}<div class="detail">${esc(meta.detail)}</div></span>` +
    `<span class="lat">${lat}</span>`;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

function watchRun(runId, submitted) {
  if (activeStream) activeStream.close();
  hideRateBanner();
  $("placeholder").style.display = "none";
  $("run-view").style.display = "block";
  $("feed").innerHTML = '<div class="waiting"><span class="live-dot"></span>verbinde …</div>';
  $("run-answer").style.display = "none";
  $("run-status").textContent = submitted ? submitted.status : "…";
  $("run-status").className = `status ${submitted ? submitted.status : ""}`;
  $("run-id").textContent = runId + (submitted && submitted.title ? ` · ${submitted.title}` : "");
  $("run-cost").textContent = "";
  $("narrative").innerHTML = "";
  $("cost-breakdown").innerHTML = "";

  const es = new EventSource(`/api/runs/stream?run_id=${runId}`);
  activeStream = es;
  let firstEvent = true;

  es.addEventListener("step", (e) => {
    if (firstEvent) {
      $("feed").innerHTML = "";
      $("run-status").textContent = "running";
      $("run-status").className = "status running";
      firstEvent = false;
    }
    appendFeedLine(JSON.parse(e.data));
  });

  es.addEventListener("done", async (e) => {
    es.close();
    activeStream = null;
    const done = JSON.parse(e.data);
    $("run-status").textContent = done.status;
    $("run-status").className = `status ${done.status}`;
    $("run-answer").style.display = "block";
    $("run-answer").textContent = done.answer || "(keine Antwort)";
    await loadExplanation(runId);
    await refreshState();
  });

  es.addEventListener("stream_error", () => {
    es.close();
    activeStream = null;
    appendFeedLine({ kind: "llm_error", duration_ms: 0, payload: { error: "unbekannter Run" } });
  });
}

async function loadExplanation(runId) {
  const explanation = await api(`/api/explain?run_id=${runId}`);
  $("run-cost").textContent = money(explanation.cost.total_usd);
  $("narrative").innerHTML = explanation.narrative.map((line) => `<li>${esc(line)}</li>`).join("");
  $("cost-breakdown").innerHTML = explanation.cost.items
    .map((item) => `<div><span class="k">${esc(item.kind)} · ${esc(item.detail)}:</span> ${money(item.cost_usd)}</div>`)
    .join("");
}

$("fire-x3").addEventListener("click", fireNoisyNeighborX3);
loadScenarios().then(refreshState).catch((err) => {
  $("placeholder").textContent = `Fehler: ${err.message}`;
});
