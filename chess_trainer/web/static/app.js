import { Chessground } from "/vendor/chessground/chessground.min.js";

const root = document.getElementById("app");

// --- tiny fetch helpers ------------------------------------------------------

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}
async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}
async function apiPut(path, body) {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// --- router ------------------------------------------------------------------

function parseHash() {
  const h = location.hash.replace(/^#\/?/, "").split("?")[0];
  const parts = h.split("/").filter(Boolean);
  if (parts.length === 0) return { screen: "start" };
  if (parts[0] === "s" && parts[2] === "progress") return { screen: "progress", sessionId: +parts[1] };
  if (parts[0] === "s" && parts[2] === "review") return { screen: "review", sessionId: +parts[1] };
  if (parts[0] === "s" && parts[2] === "summary") return { screen: "summary", sessionId: +parts[1] };
  return { screen: "start" };
}

function navigate(hash) {
  location.hash = hash;
}

window.addEventListener("hashchange", render);
window.addEventListener("DOMContentLoaded", render);

// Screens that register document-level listeners (currently just the review
// screen's keyboard shortcuts) register a teardown here. render() always
// tears down the previous screen's listeners before building the new one -
// centralizing this (rather than each screen cleaning up on its own, e.g.
// via a one-off hashchange listener) avoids a real bug that happened with
// the per-screen approach: save() both navigates (triggering render() async
// via hashchange) AND calls render() directly for an immediate update, so a
// new screen's listener could get attached before the old one's hashchange-
// triggered cleanup ran, leaving two keydown listeners active at once. The
// stale one's closure still pointed at the previous (now-detached) textarea,
// so `document.activeElement === textArea` was always false for it - it
// would wrongly treat the real textarea as unfocused and swallow Space
// before the new, correct listener ever saw the keystroke.
let activeScreenCleanup = null;

async function render() {
  if (activeScreenCleanup) { activeScreenCleanup(); activeScreenCleanup = null; }
  const route = parseHash();
  try {
    if (route.screen === "start") await renderStart();
    else if (route.screen === "progress") await renderProgress(route.sessionId);
    else if (route.screen === "review") await renderReview(route.sessionId);
    else if (route.screen === "summary") await renderSummary(route.sessionId);
  } catch (e) {
    root.innerHTML = "";
    root.appendChild(el("div", { class: "panel" }, [
      el("h2", {}, "Something went wrong"),
      el("p", {}, String(e)),
      el("button", { onclick: () => navigate("#/start") }, "Back to start"),
    ]));
    console.error(e);
  }
}

// --- Screen 1: Start -----------------------------------------------------

async function renderStart() {
  root.innerHTML = "";
  const [config, sessions] = await Promise.all([apiGet("/api/config"), apiGet("/api/sessions")]);

  const usernameInput = el("input", { type: "text", id: "username", value: config.default_username || "" });
  const nGamesInput = el("input", { type: "number", id: "n_games", min: "1", max: "50", value: "10" });
  const maxPosInput = el("input", { type: "number", id: "max_pos", min: "1", max: "100", value: "30" });
  const sevBlunder = el("input", { type: "checkbox", id: "sev_blunder", checked: "checked" });
  const sevMistake = el("input", { type: "checkbox", id: "sev_mistake", checked: "checked" });
  const sevInaccuracy = el("input", { type: "checkbox", id: "sev_inaccuracy" });

  const startPanel = el("div", { class: "panel" }, [
    el("h1", {}, "Chess Trainer: Review"),
    el("p", { class: "muted" }, `Time class: ${config.time_class}. Reviews pull your flagged moves and let you label what went wrong.`),
    el("label", { for: "username" }, "Chess.com username"),
    usernameInput,
    el("label", { for: "n_games" }, "Games to review (most recent)"),
    nGamesInput,
    el("label", {}, "Include"),
    el("div", { class: "checkbox-row" }, [sevBlunder, el("label", { style: "margin:0" }, "Blunders")]),
    el("div", { class: "checkbox-row" }, [sevMistake, el("label", { style: "margin:0" }, "Mistakes")]),
    el("div", { class: "checkbox-row" }, [sevInaccuracy, el("label", { style: "margin:0" }, "Inaccuracies")]),
    el("label", { for: "max_pos" }, "Max positions this session"),
    maxPosInput,
    el("div", { class: "btn-row" }, [
      el("button", {
        class: "primary",
        onclick: async () => {
          const severities = [];
          if (sevBlunder.checked) severities.push("blunder");
          if (sevMistake.checked) severities.push("mistake");
          if (sevInaccuracy.checked) severities.push("inaccuracy");
          if (!severities.length) { alert("Pick at least one severity."); return; }
          const { session_id, job_id } = await apiPost("/api/sessions", {
            username: usernameInput.value.trim(),
            n_games: +nGamesInput.value,
            severities,
            max_positions: +maxPosInput.value || null,
          });
          navigate(`#/s/${session_id}/progress?job=${job_id}`);
        },
      }, "Start review"),
    ]),
  ]);
  root.appendChild(startPanel);

  if (sessions.length) {
    const list = el("div", { class: "panel" }, [el("h3", {}, "Resume")]);
    for (const s of sessions) {
      list.appendChild(el("div", { class: "session-row" }, [
        el("span", {}, `${s.chesscom_username} - ${s.labeled}/${s.total} labeled`),
        el("button", { onclick: () => navigate(`#/s/${s.session_id}/review`) }, "Continue"),
      ]));
    }
    root.appendChild(list);
  }
}

// --- Screen 2: Progress --------------------------------------------------

async function renderProgress(sessionId) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const jobId = params.get("job");

  root.innerHTML = "";
  const stageLabel = el("div", { class: "muted" }, "Starting...");
  const bar = el("div", { class: "progress-bar-fill", style: "width:0%" });
  const startNowBtn = el("button", { class: "primary", disabled: "disabled" }, "Start reviewing now");
  startNowBtn.addEventListener("click", () => navigate(`#/s/${sessionId}/review`));

  root.appendChild(el("div", { class: "panel" }, [
    el("h2", {}, "Fetching and analyzing..."),
    stageLabel,
    el("div", { class: "progress-bar-track" }, bar),
    el("div", { class: "btn-row" }, [startNowBtn]),
  ]));

  if (!jobId) { navigate(`#/s/${sessionId}/review`); return; }

  const poll = async () => {
    let job;
    try { job = await apiGet(`/api/jobs/${jobId}`); }
    catch { navigate(`#/s/${sessionId}/review`); return; }

    const stageNames = { syncing: "Fetching games from chess.com", analyzing: "Analyzing with Stockfish", tagging: "Tagging blunders", done: "Done", error: "Error" };
    stageLabel.textContent = `${stageNames[job.stage] || job.stage}${job.total ? ` (${job.done}/${job.total})` : ""}`;
    bar.style.width = job.total ? `${Math.round((job.done / job.total) * 100)}%` : (job.stage === "done" ? "100%" : "10%");

    // Check if anything's ready to review yet.
    try {
      const { queue } = await apiGet(`/api/sessions/${sessionId}/queue`);
      if (queue.length > 0) startNowBtn.removeAttribute("disabled");
    } catch { /* ignore */ }

    if (job.stage === "done") { navigate(`#/s/${sessionId}/review`); return; }
    if (job.stage === "error") {
      root.appendChild(el("div", { class: "panel" }, [el("p", { style: "color:var(--danger)" }, job.message)]));
      return;
    }
    setTimeout(poll, 1000);
  };
  poll();
}

// --- Screen 3: Review ------------------------------------------------------

const TAG_KEYS_ORDER = ["hung_piece", "missed_capture", "unsound_sacrifice", "king_safety",
                        "ignored_threat", "self_obstruction", "wasted_tempo", "other"];

async function renderReview(sessionId) {
  const { queue } = await apiGet(`/api/sessions/${sessionId}/queue`);
  const pending = queue.filter((q) => !q.done);
  const doneCount = queue.length - pending.length;

  if (pending.length === 0) {
    navigate(`#/s/${sessionId}/summary`);
    return;
  }

  const current = pending[0];
  const [pos, tags] = await Promise.all([
    apiGet(`/api/positions/${current.game_id}/${current.ply}`),
    apiGet("/api/tags"),
  ]);

  root.innerHTML = "";
  const positionIndex = doneCount + 1;
  const total = queue.length;

  const header = el("div", { class: "panel" }, [
    el("div", {}, `Position ${positionIndex} / ${total}`),
    el("div", { class: "progress-bar-track" }, el("div", {
      class: "progress-bar-fill", style: `width:${Math.round((doneCount / total) * 100)}%`,
    })),
    el("div", { class: "muted" }, `Game: ${pos.played_at.slice(0, 10)} · ${pos.result} as ${pos.my_colour} · `, ),
  ]);
  header.lastChild.appendChild(el("a", { href: `https://www.chess.com/game/live/${pos.chesscom_uuid}`, target: "_blank" }, "chess.com game ↗"));

  const boardWrap = el("div", { class: "board-wrap" });
  const beforeAfterBtn = el("button", {}, "Show After (Space)");
  let showingAfter = false;

  const sevClass = pos.severity === "blunder" ? "severity-blunder" : "severity-mistake";
  const prevMoveNote = pos.prev_from
    ? `Opponent just played a move to ${pos.prev_to}.`
    : "(First move of the game.)";

  const meta = el("div", { class: "position-meta" }, [
    el("div", { class: "san" }, pos.move_label),
    el("div", { class: sevClass }, `${pos.severity} · ${pos.phase}`),
    el("div", {}, `Win% ${Math.round(pos.win_pct_before)} → ${Math.round(pos.win_pct_after)} (−${Math.round(pos.win_pct_drop || 0)})`),
    pos.time_spent_s != null ? el("div", { class: "muted" }, `Spent ${pos.time_spent_s.toFixed(1)}s on this move`) : el("div"),
    el("div", { class: "muted" }, prevMoveNote),
  ]);

  const textArea = el("textarea", { placeholder: "What went wrong? (optional if you pick a tag below)" });
  textArea.value = pos.existing_label.text || "";

  const selectedTags = new Set(pos.existing_label.tags || []);
  const chipsWrap = el("div", { class: "chips" });
  const chipEls = {};
  tags.forEach((t, i) => {
    const chipNode = el("div", {
      class: "chip" + (selectedTags.has(t.key) ? " selected" : ""),
      title: t.tooltip,
      onclick: () => {
        if (selectedTags.has(t.key)) selectedTags.delete(t.key); else selectedTags.add(t.key);
        chipNode.classList.toggle("selected");
      },
    }, [el("span", { class: "key" }, String(i + 1)), t.label]);
    chipEls[t.key] = chipNode;
    chipsWrap.appendChild(chipNode);
  });

  const unsureCheckbox = el("input", { type: "checkbox", id: "unsure" });
  unsureCheckbox.checked = pos.existing_label.confidence === "unsure";

  const startTime = Date.now();

  async function save(status) {
    const seconds_spent = (Date.now() - startTime) / 1000;
    await apiPut(`/api/labels/${current.game_id}/${current.ply}`, {
      text: textArea.value.trim() || null,
      tags: Array.from(selectedTags),
      confidence: unsureCheckbox.checked ? "unsure" : null,
      status,
      session_id: sessionId,
      seconds_spent,
    });
    navigate(`#/s/${sessionId}/review`);
    render();
  }

  const saveBtn = el("button", { class: "primary" }, "Save & next (Ctrl+Enter)");
  saveBtn.addEventListener("click", () => save("labeled"));
  const skipBtn = el("button", {}, "Skip");
  skipBtn.addEventListener("click", () => save("skipped"));

  const form = el("div", { class: "form-col" }, [
    meta,
    el("label", {}, "What went wrong?"),
    textArea,
    el("label", {}, "Or pick (number keys 1-8):"),
    chipsWrap,
    el("div", { class: "checkbox-row" }, [unsureCheckbox, el("label", { style: "margin:0", for: "unsure" }, "Not sure")]),
    el("div", { class: "btn-row" }, [skipBtn, saveBtn]),
  ]);

  root.appendChild(header);
  root.appendChild(el("div", { class: "review-layout" }, [
    el("div", { class: "board-col" }, [boardWrap, el("div", { class: "btn-row" }, [beforeAfterBtn])]),
    form,
  ]));

  const cg = Chessground(boardWrap, {
    fen: pos.fen_before,
    orientation: pos.my_colour,
    viewOnly: true,
    coordinates: true,
    lastMove: pos.prev_from ? [pos.prev_from, pos.prev_to] : undefined,
    drawable: {
      autoShapes: [{ orig: pos.move_from, dest: pos.move_to, brush: "red" }],
    },
  });

  beforeAfterBtn.addEventListener("click", () => {
    showingAfter = !showingAfter;
    if (showingAfter) {
      cg.set({ fen: pos.fen_after, lastMove: [pos.move_from, pos.move_to], drawable: { autoShapes: [] } });
      beforeAfterBtn.textContent = "Show Before (Space)";
    } else {
      cg.set({ fen: pos.fen_before, lastMove: pos.prev_from ? [pos.prev_from, pos.prev_to] : undefined,
                drawable: { autoShapes: [{ orig: pos.move_from, dest: pos.move_to, brush: "red" }] } });
      beforeAfterBtn.textContent = "Show After (Space)";
    }
  });

  function keyHandler(e) {
    const inTextarea = document.activeElement === textArea;
    if (e.key === " " && !inTextarea) { e.preventDefault(); beforeAfterBtn.click(); }
    else if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); save("labeled"); }
    else if (!inTextarea && /^[1-8]$/.test(e.key)) {
      const t = tags[+e.key - 1];
      if (t) chipEls[t.key].click();
    } else if (e.key === "Escape" && inTextarea) { textArea.blur(); }
  }
  document.addEventListener("keydown", keyHandler);
  activeScreenCleanup = () => document.removeEventListener("keydown", keyHandler);
}

// --- Screen 4: Summary -----------------------------------------------------

async function renderSummary(sessionId) {
  const summary = await apiGet(`/api/sessions/${sessionId}/summary`);
  root.innerHTML = "";

  const tagRows = (counts) => TAG_KEYS_ORDER.map((k) => `${k}: ${counts[k] || 0}`).join(" · ");

  const panel = el("div", { class: "panel" }, [
    el("h2", {}, "Session summary"),
    el("p", {}, `${summary.total} positions: ${summary.labeled} labeled, ${summary.skipped} skipped, ${summary.unsure} marked unsure.`),
    el("h3", {}, "Your tags"),
    el("p", { class: "muted" }, tagRows(summary.your_tag_counts)),
    el("h3", {}, "Tagger's tags (same positions)"),
    el("p", { class: "muted" }, tagRows(summary.tagger_tag_counts)),
    el("h3", {}, "Agreement"),
    el("p", {}, summary.agreement_pct != null
      ? `${summary.agreement_pct}% overlap across ${summary.compared} compared positions.`
      : "Nothing to compare yet."),
  ]);
  root.appendChild(panel);

  if (summary.disagreements.length) {
    const table = el("table", {}, [
      el("tr", {}, [el("th", {}, "Position"), el("th", {}, "You said"), el("th", {}, "Tagger said")]),
    ]);
    for (const d of summary.disagreements) {
      table.appendChild(el("tr", {}, [
        el("td", {}, el("a", { href: `#/s/${sessionId}/review` }, `g${d.game_id} ply${d.ply}`)),
        el("td", {}, d.your_tags.join(", ") || "-"),
        el("td", {}, d.tagger_tags.join(", ") || "-"),
      ]));
    }
    root.appendChild(el("div", { class: "panel" }, [el("h3", {}, "Disagreements"), table]));
  }

  root.appendChild(el("div", { class: "btn-row" }, [
    el("button", { class: "primary", onclick: () => navigate("#/start") }, "Review more"),
  ]));
}
