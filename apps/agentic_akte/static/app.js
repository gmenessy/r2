"use strict";

const $ = (id) => document.getElementById(id);
const currentCase = () => $("case").value.trim() || "default";

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
    body: JSON.stringify({ case: currentCase(), ...payload }),
  });

const get = (path) =>
  api(`${path}?case=${encodeURIComponent(currentCase())}`);

function renderGate(decision) {
  const el = $("gate-card");
  el.className = "gate " + decision.mode;
  const findings = (decision.findings || [])
    .map((f) => `<li><strong>${f.gate}</strong> (${f.severity}): ${f.message}</li>`)
    .join("");
  const overdue = (decision.overdue_deadlines || [])
    .map((d) => `<li>⏰ Überfällig: ${d.statement}</li>`)
    .join("");
  el.innerHTML =
    `<span class="mode">${decision.mode.replace(/_/g, " ")}</span>` +
    (findings || overdue ? `<ul>${findings}${overdue}</ul>` : " — keine Einwände.") +
    (decision.suggested_alternative ? `<p>💡 ${decision.suggested_alternative}</p>` : "");
}

async function refresh() {
  const { cases } = await api("/api/cases");
  $("case-list").innerHTML = cases.map((c) => `<option value="${c}">`).join("");

  const { deadlines } = await get("/api/deadlines");
  $("deadline-list").innerHTML =
    deadlines
      .map(
        (d) =>
          `<div class="deadline ${d.status}"><span>${d.statement}</span>` +
          `<span>${d.status === "overdue" ? "⛔ überfällig" : "🕓 offen"}</span></div>`
      )
      .join("") || '<span style="color:var(--muted);font-size:.82rem">Keine Fristen.</span>';

  const { cards } = await get("/api/memory");
  $("memory-list").innerHTML =
    cards
      .slice()
      .reverse()
      .map(
        (card) =>
          `<div class="card ${card.memory_type}">` +
          `<span class="type">${card.memory_type}${card.case_id ? "" : " · global"} · ab ${card.valid_from}</span>` +
          `<div>${card.statement}</div></div>`
      )
      .join("") || '<span style="color:var(--muted);font-size:.82rem">Noch keine Erinnerungen.</span>';

  const { events } = await get("/api/timeline");
  $("timeline-list").innerHTML =
    events
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="event"><span class="meta">${e.timestamp.slice(0, 19)} · ${e.event_type} · ${e.source}</span>` +
          `<div>${e.content}</div></div>`
      )
      .join("") || '<span style="color:var(--muted);font-size:.82rem">Noch keine Ereignisse.</span>';
}

const handle = (fn) => async () => {
  try {
    await fn();
    await refresh();
  } catch (error) {
    alert(error.message);
  }
};

$("btn-check").addEventListener(
  "click",
  handle(async () => {
    const docs = $("action-docs").value.split(",").map((s) => s.trim()).filter(Boolean);
    const decision = await post("/api/check", {
      action_type: $("action-type").value,
      documents: docs,
      error_signature: $("action-signature").value.trim() || null,
    });
    renderGate(decision);
  })
);

$("btn-doc").addEventListener(
  "click",
  handle(() =>
    post("/api/documents", {
      name: $("doc-name").value.trim(),
      summary: $("doc-summary").value.trim(),
      fragile: $("doc-fragile").checked,
    })
  )
);

$("btn-decision").addEventListener(
  "click",
  handle(() => post("/api/decisions", { text: $("decision-text").value.trim() }))
);

$("btn-deadline").addEventListener(
  "click",
  handle(() =>
    post("/api/deadlines", {
      description: $("deadline-desc").value.trim(),
      due_date: $("deadline-date").value,
    })
  )
);

$("btn-failed").addEventListener(
  "click",
  handle(() =>
    post("/api/failed-analyses", {
      text: $("failed-text").value.trim(),
      signature: $("failed-signature").value.trim(),
      alternative: $("failed-alternative").value.trim(),
    })
  )
);

$("btn-consolidate").addEventListener(
  "click",
  handle(async () => {
    const report = await post("/api/consolidate", {});
    alert(
      `Konsolidierung: ${report.deduplicated.length} Duplikate, ` +
        `${report.contradictions.length} Widersprüche, ${report.archived.length} archiviert.`
    );
  })
);

$("case").addEventListener("change", refresh);
refresh();
