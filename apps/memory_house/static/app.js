"use strict";

const $ = (id) => document.getElementById(id);
let currentRoom = "wohnzimmer";

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

function renderGate(result) {
  const el = $("gate-card");
  el.className = "gate " + result.mode;
  const findings = (result.findings || [])
    .map((f) => `<li><strong>${f.gate}</strong>: ${f.message}</li>`)
    .join("");
  el.innerHTML =
    `<span class="mode">${result.mode.replace(/_/g, " ")}` +
    (result.executed ? " · ausgeführt ✅" : " · NICHT ausgeführt 🛑") +
    `</span>` +
    (findings ? `<ul>${findings}</ul>` : " — das Haus hat keine Einwände.") +
    (result.suggested_alternative ? `<p>💡 Das Haus schlägt vor: ${result.suggested_alternative}</p>` : "");
}

async function refreshRooms() {
  const { rooms } = await api("/api/rooms");
  $("room-list").innerHTML = "";
  for (const room of rooms) {
    const status = await api(`/api/room?room=${encodeURIComponent(room)}`);
    const button = document.createElement("button");
    button.className = "room" + (room === currentRoom ? " active" : "");
    button.innerHTML =
      `<span>${room}</span>` +
      `<span class="badges">${"⚠️".repeat(status.risks)}${"📚".repeat(status.lessons)}</span>`;
    button.addEventListener("click", () => {
      currentRoom = room;
      refreshRooms();
      refreshRoom();
    });
    $("room-list").appendChild(button);
  }
}

async function refreshRoom() {
  $("room-title").textContent = `Aktionen · ${currentRoom}`;
  const status = await api(`/api/room?room=${encodeURIComponent(currentRoom)}`);
  $("memory-list").innerHTML =
    status.memory_cards
      .slice()
      .reverse()
      .map(
        (card) =>
          `<div class="card ${card.memory_type}">` +
          `<span class="type">${card.memory_type}${card.case_id ? "" : " · ganzes Haus"}</span>` +
          `<div>${card.statement}</div></div>`
      )
      .join("") || '<span style="color:var(--muted);font-size:.8rem">Dieser Raum hat noch nichts erlebt.</span>';
  $("event-list").innerHTML =
    status.recent_events
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="event"><span class="meta">${e.timestamp.slice(11, 19)} · ${e.event_type}</span>` +
          `<div>${e.content}</div></div>`
      )
      .join("") || '<span style="color:var(--muted);font-size:.8rem">Keine Ereignisse.</span>';
}

document.querySelectorAll(".actions button").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;
    const kind = button.dataset.kind;
    const payload = {
      room: currentRoom,
      action_type: action,
      context: { call_aktiv: $("call-active").checked },
    };
    if (kind === "signature") payload.signature = action;
    if (kind === "device") {
      payload.device = action;
      payload.action_type = "sicherung_schalten";
    }
    try {
      renderGate(await post("/api/attempt", payload));
      await refreshRoom();
      await refreshRooms();
    } catch (error) {
      alert(error.message);
    }
  });
});

refreshRooms().then(refreshRoom);
