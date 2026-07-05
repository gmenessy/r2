"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
let currentCase = null;
let selected = null;

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}

// -- Kraft-Graph (leichtgewichtige Simulation) ------------------------------

function layout(nodes, edges, width, height) {
  const rng = mulberry(nodes.length + 7);
  nodes.forEach((n) => {
    n.x = width / 2 + (rng() - 0.5) * width * 0.6;
    n.y = height / 2 + (rng() - 0.5) * height * 0.6;
    n.vx = 0;
    n.vy = 0;
  });
  const idx = Object.fromEntries(nodes.map((n) => [n.id, n]));
  for (let step = 0; step < 300; step++) {
    // Abstoßung
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const f = 5000 / d2;
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
    }
    // Federn entlang Kanten
    edges.forEach((e) => {
      const a = idx[e.src], b = idx[e.dst];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 110) * 0.02;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    });
    // Zentrierung + Dämpfung
    nodes.forEach((n) => {
      n.vx += (width / 2 - n.x) * 0.002;
      n.vy += (height / 2 - n.y) * 0.002;
      n.x += Math.max(-8, Math.min(8, n.vx));
      n.y += Math.max(-8, Math.min(8, n.vy));
      n.vx *= 0.85; n.vy *= 0.85;
      n.x = Math.max(30, Math.min(width - 30, n.x));
      n.y = Math.max(30, Math.min(height - 30, n.y));
    });
  }
}

function mulberry(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function el(name, attrs, parent) {
  const e = document.createElementNS(SVG_NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

async function renderGraph() {
  const svg = $("svg");
  svg.innerHTML = "";
  const { width, height } = svg.getBoundingClientRect();
  const data = await api(`/api/graph?case=${encodeURIComponent(currentCase)}`);
  if (!data.nodes.length) {
    el("text", { x: width / 2 - 60, y: height / 2, fill: "#8b949e" }, svg).textContent = "Keine Karten.";
    return;
  }
  layout(data.nodes, data.edges, width, height);
  const idx = Object.fromEntries(data.nodes.map((n) => [n.id, n]));

  data.edges.forEach((e) => {
    const a = idx[e.src], b = idx[e.dst];
    if (!a || !b) return;
    el("line", { class: `edge ${e.rel}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y }, svg);
  });
  data.nodes.forEach((n) => {
    const r = 10 + n.trust * 8; // Knotengröße ~ Vertrauen
    el("circle", {
      class: `card-node ${n.status}` + (selected === n.id ? " selected" : ""),
      cx: n.x, cy: n.y, r, fill: n.color, "data-id": n.id,
    }, svg).addEventListener("click", () => selectCard(n.id));
    const label = el("text", { class: "node-label", x: n.x + r + 3, y: n.y + 3 }, svg);
    label.textContent = n.label;
  });
}

// -- Detail-Panel ------------------------------------------------------------

async function selectCard(id) {
  selected = id;
  await renderGraph();
  const { card, relationships } = await api(`/api/card?id=${encodeURIComponent(id)}`);
  const rels = relationships.map((r) => {
    const arrow = r.direction === "out" ? "→" : "←";
    return `<div class="rel"><b>${r.rel}</b> ${arrow} ${r.other_statement || r.other_id}` +
      (r.meta && r.meta.winner ? ` <span style="color:var(--muted)">(bestätigt)</span>` : "") + `</div>`;
  }).join("") || '<span style="color:var(--muted)">keine</span>';
  $("detail").innerHTML =
    `<h2>Karte</h2>` +
    `<p class="detail"><b>${card.statement}</b></p>` +
    `<p><span class="badge">${card.memory_type}</span>` +
    `<span class="badge">status: ${card.status}</span>` +
    `<span class="badge">trust ${card.trust.toFixed(2)}</span>` +
    `<span class="badge">ab ${card.valid_from}</span></p>` +
    `<h2>Beziehungen (Provenienz &amp; Graph)</h2>${rels}`;
}

// -- Live-Gatekeeper ---------------------------------------------------------

async function runGate() {
  const files = $("files").value.split(",").map((s) => s.trim()).filter(Boolean);
  const body = {
    case: currentCase,
    action_type: $("action").value,
    error_signature: $("sig").value.trim() || null,
    files,
    context: $("react").checked
      ? { case_id: currentCase, package_json: { dependencies: { react: "^18" } } }
      : null,
  };
  const r = await fetch("/api/simulate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json();
  const gate = $("gate");
  gate.className = "gate " + d.mode;
  const findings = (d.findings || []).map((f) =>
    `<li><b>${f.gate}</b> (${f.severity}): ${f.message}</li>`).join("");
  gate.innerHTML = `<span class="mode">${d.mode.replace(/_/g, " ")}</span>` +
    (findings ? `<ul>${findings}</ul>` : " — keine Einwände.") +
    (d.suggested_alternative ? `<p>💡 ${d.suggested_alternative}</p>` : "");
  await renderGraph();
}

// -- Bootstrap ---------------------------------------------------------------

async function init() {
  const { cases, default: def } = await api("/api/cases");
  currentCase = def || cases[0];
  $("case").innerHTML = cases.map((c) => `<option ${c === currentCase ? "selected" : ""}>${c}</option>`).join("");
  $("case").addEventListener("change", (e) => { currentCase = e.target.value; selected = null; renderGraph(); });
  $("run").addEventListener("click", runGate);
  window.addEventListener("resize", () => renderGraph());
  await renderGraph();
}

init();
