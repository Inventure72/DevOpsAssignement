import { HostAudio } from "./host-audio.mjs";
import { api, configuredOrigin, newRequestId } from "./transport.mjs";

const $ = (id) => document.getElementById(id);
const SESSION_KEY = "repeat-demo-player";
const POLL_MS = 1000;
const HEARTBEAT_MS = 4500;

const state = {
  session: null,
  lobby: null,
  game: null,
  access: {},
  notice: "",
  error: { rooms: "", game: "" },
};

let rooms;
let game;
let audio;
let screen;
let rosterKey;
let scoresKey;
let draftRound;
let selectedSong;
let readyKey;
let readyWork;
let submittedRound;
let searchGeneration = 0;
let nextHeartbeat = 0;

function setText(id, value) {
  if ($(id).textContent !== value) $(id).textContent = value;
}

function notify(message) {
  state.notice = message;
  renderStatus();
}

function renderStatus() {
  const message = state.notice || state.error.rooms || state.error.game;
  $("status").hidden = !message;
  setText("status", message);
}

function saveSession() {
  if (state.session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(state.session));
  else sessionStorage.removeItem(SESSION_KEY);
}

function readSession() {
  try {
    const session = JSON.parse(sessionStorage.getItem(SESSION_KEY));
    return session && typeof session.token === "string" && typeof session.room_id === "string" &&
      typeof session.player_id === "string" && /^[A-Z0-9]{6}$/.test(session.code) ? session : null;
  } catch {
    return null;
  }
}

function setRoom(session) {
  audio.releaseRoom();
  state.session = session;
  state.lobby = null;
  state.error = { rooms: "", game: "" };
  rosterKey = null;
  $("invite-link").hidden = true;
  nextHeartbeat = 0;
  selectGame(session?.gameId ?? null);
  const url = new URL("/", location.origin);
  if (session) url.searchParams.set("room", session.code);
  history.replaceState(null, "", url);
  saveSession();
  render();
}

function selectGame(id) {
  if (state.session) {
    state.session.gameId = id;
    saveSession();
  }
  state.game = null;
  state.access = {};
  state.error.game = "";
  draftRound = undefined;
  searchGeneration++;
  readyKey = undefined;
  submittedRound = undefined;
  scoresKey = undefined;
  audio.selectGame(id);
}

const roomPath = (session) => `/api/rooms/${encodeURIComponent(session.room_id)}`;
const roomFetch = (session, path, options) =>
  rooms(roomPath(session) + path, { ...options, token: session.token });

function renewGameTicket(session, access, gameId) {
  access.renewal ??= roomFetch(session, `/games/${encodeURIComponent(gameId)}/session`, {
    method: "POST",
  }).then(({ token, expires_in_seconds }) => {
    access.token = token;
    access.expires = Date.now() + expires_in_seconds * 1000;
    return token;
  }).finally(() => { access.renewal = null; });
  return access.renewal;
}

async function gameFetch(session, gameId, path, options) {
  if (state.session !== session || session.gameId !== gameId) {
    throw new Error("This game has changed.");
  }
  const access = state.access;
  if (access.token && access.expires > Date.now()) {
    if (access.expires < Date.now() + 15_000) {
      renewGameTicket(session, access, gameId).catch(() => {});
    }
  } else {
    await renewGameTicket(session, access, gameId);
  }
  const token = access.token;
  try {
    return await game(`/api/games/${encodeURIComponent(gameId)}${path}`, { ...options, token });
  } catch (error) {
    if (error.status === 401 && access.token === token) access.expires = 0;
    throw error;
  }
}

function applyGameId(id) {
  if (state.session.gameId !== id) selectGame(id);
  state.session.pendingStart = null;
  saveSession();
  render();
}

async function pollRooms() {
  const session = state.session;
  if (!session) return;

  const updateHeartbeat = Date.now() >= nextHeartbeat;
  if (updateHeartbeat) nextHeartbeat = Date.now() + HEARTBEAT_MS;
  try {
    const lobbyWork = roomFetch(session, "/lobby");
    const heartbeatWork = updateHeartbeat ? roomFetch(session, "/heartbeat", {
      method: "POST",
    }).catch((error) => error) : null;
    const lobby = await lobbyWork;
    if (state.session !== session) return;
    state.lobby = lobby;
    const host = Boolean(lobby.players.find((player) => player.id === session.player_id)?.is_host);
    if (session.host !== host) {
      session.host = host;
      saveSession();
    }
    const heartbeatError = await heartbeatWork;
    if (state.session !== session) return;
    state.error.rooms = heartbeatError?.message ?? "";
    render();

    if (lobby.state === "playing" && !session.gameId) {
      const result = await roomFetch(session, "/game/sync", { method: "POST" });
      if (state.session === session && !session.gameId && result.game &&
          result.game.game_id !== session.closedGameId) {
        applyGameId(result.game.game_id);
      }
    }
  } catch (error) {
    if (state.session !== session) return;
    if (error.status === 401 || error.status === 404 || error.status === 410) {
      setRoom(null);
      $("join-code").value = session.code;
    } else {
      state.error.rooms = error.message;
    }
    renderStatus();
    if (state.session !== session) notify(error.message);
  }
}

function updateRoundReadiness(view, session, request) {
  const round = view.round;
  if (!round || view.phase !== "ready" || readyWork ||
      (session.host && (!audio.enabled || !audio.has(round.audio_candidate_id)))) return;
  const key = `${round.id}:${round.readiness_generation}`;
  if (readyKey === key || round.ready_player_ids.includes(session.player_id)) return;
  readyWork = request(`/rounds/${encodeURIComponent(round.id)}/ready`, {
    method: "POST",
    body: { readiness_generation: round.readiness_generation },
  }).then(() => {
    if (state.session === session && session.gameId === view.id) readyKey = key;
  }).catch((error) => {
    if (error.status !== 409 && state.session === session && session.gameId === view.id) {
      state.error.game = error.message;
      renderStatus();
    }
  }).finally(() => { readyWork = null; });
}

async function pollGame() {
  const session = state.session;
  const id = session?.gameId;
  if (!id) return;
  const request = (path, options) => gameFetch(session, id, path, options);
  const started = performance.now();
  try {
    const view = await request("");
    if (state.session !== session || session.gameId !== id) return;
    view.clientAt = (started + performance.now()) / 2;
    state.game = view;
    state.error.game = "";
    render();
    if (session.host) {
      if (terminal(view)) audio.releaseRoom();
      else {
        audio.play(view, estimatedTime(view));
        audio.prepare(view, request).catch((error) => {
          if (state.session === session && session.gameId === id) {
            state.error.game = error.message;
            renderStatus();
          }
        });
      }
    }
    updateRoundReadiness(view, session, request);
  } catch (error) {
    if (state.session !== session || session.gameId !== id) return;
    state.error.game = error.message;
    renderStatus();
  }
}

function poll(work) {
  const loop = () => work().finally(() => setTimeout(loop, POLL_MS));
  loop();
}

function terminal(view) {
  return ["completed", "aborted"].includes(view?.status);
}

function estimatedTime(view) {
  return view.server_now_ms + performance.now() - view.clientAt;
}

function showScreen(id) {
  if (screen === id) return;
  for (const name of ["entry", "lobby", "game"]) $(name).hidden = name !== id;
  if (id !== "entry") $(`${id}-title`).focus({ preventScroll: true });
  screen = id;
}

function listRow(label, value) {
  const row = document.createElement("li");
  const text = document.createElement("span");
  const detail = document.createElement("span");
  text.textContent = label;
  detail.textContent = value;
  row.append(text, detail);
  return row;
}

function renderLobby() {
  const { lobby, session } = state;
  showScreen("lobby");
  setText("room-code", session.code);
  if (!lobby) {
    setText("lobby-status", "Reconnecting to your room…");
    $("roster").replaceChildren();
    $("start").hidden = true;
    return;
  }

  const roster = JSON.stringify(lobby.players.map(({ nickname, id, is_host, connected }) =>
    [nickname, id, is_host, connected]));
  if (roster !== rosterKey) {
    rosterKey = roster;
    $("roster").replaceChildren(...lobby.players.map((player) => listRow(
      player.nickname + (player.id === session.player_id ? " (you)" : ""),
      [player.is_host ? "Host" : "", player.connected ? "Connected" : "Away"].filter(Boolean).join(" · "),
    )));
  }
  const need = lobby.minimum_players - lobby.players.filter((player) => player.connected).length;
  const pending = Boolean(session.pendingStart);
  setText("lobby-status", lobby.state === "playing" || pending
    ? "Connecting to the game…"
    : need > 0 ? `Waiting for ${need} more ${need === 1 ? "player" : "players"}.`
    : session.host ? "Everyone’s here. Start when you’re ready." : "Waiting for the host to start.");
  $("start").hidden = !session.host || (lobby.state === "playing" && !pending);
  $("start").disabled = Boolean($("start").dataset.busy) || (need > 0 && !pending);
  setText("start", pending ? "Retry start" : "Start game");
}

function choice(type, name, text, value) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  label.className = "choice";
  input.type = type;
  input.name = name;
  input.value = value;
  label.append(input, text);
  return label;
}

function setupDraft(view) {
  if (draftRound === view.round?.id) return;
  draftRound = view.round?.id;
  selectedSong = null;
  $("song-query").value = "";
  $("songs").replaceChildren();
  $("song-results").hidden = true;
  setText("search-status", "");
  searchGeneration++;
  $("listeners").replaceChildren(
    ...view.leaderboard.map((player) => choice("checkbox", "listener", player.nickname, player.player_id)),
    choice("checkbox", "nobody", "Nobody", "nobody"),
  );
}

function renderGame() {
  const { game: view, session } = state;
  const round = view?.round;
  const ended = terminal(view);
  const answering = view?.phase === "answering" && round;
  const submitted = Boolean(round?.my_answer) || (round && submittedRound === round.id);
  const reveal = round?.reveal;
  const number = round ? `Round ${round.round_number} of ${view.requested_rounds}` : "Get ready";
  const title = ended ? (view.status === "completed" ? "Final scores" : "Game ended") : number;
  setText("game-title", title);
  showScreen("game");
  if (view) setupDraft(view);

  $("back-to-lobby").hidden = !ended;
  $("leave-game").hidden = ended;
  setText("leave-game", session.host ? "End game" : "Leave game");
  $("audio-controls").hidden = !session.host || ended || audio.enabled;
  $("guess-panel").hidden = !answering || submitted || round.excluded_player_ids.includes(session.player_id);
  $("reveal-panel").hidden = !reveal;
  $("score-panel").hidden = !view || !(ended || reveal || view.phase === "leaderboard");

  if (!view) setText("game-status", "Connecting to the game…");
  else if (view.status === "completed") setText("game-status", "");
  else if (ended) setText("game-status", view.end_reason === "host_left"
    ? "The host ended the game." : "The game ended early. Scores reflect finished rounds.");
  else if (answering) setText("game-status", submitted
    ? "Answer submitted. Waiting for the reveal."
    : round.excluded_player_ids.includes(session.player_id) ? "Watch this round and rejoin the next." : "Listen and make your guess.");
  else if (view.phase === "setup") setText("game-status", session.host
    ? `Preparing audio… ${view.preparation.checked} of ${view.preparation.total} clips checked.`
    : "Waiting for the host to prepare audio…");
  else if (view.phase === "countdown") setText("game-status", "The clip is about to play.");
  else if (view.phase === "ready") setText("game-status", "Waiting for players to get ready…");
  else setText("game-status", reveal ? "Here’s what was playing." : "");

  if (reveal) {
    setText("revealed-song", `${reveal.song.title} — ${reveal.song.artist}`);
    const names = view.leaderboard
      .filter((player) => reveal.listener_ids.includes(player.player_id))
      .map((player) => player.nickname);
    setText("revealed-listeners", names.length ? `On repeat for ${names.join(", ")}` : "Nobody had this on repeat.");
    setText("revealed-points", `You earned ${reveal.my_answer.points} points.`);
  }
  if (view) {
    const scores = JSON.stringify(view.leaderboard);
    if (scores !== scoresKey) {
      scoresKey = scores;
      $("scores").replaceChildren(...view.leaderboard.map(({ rank, nickname, score }) =>
        listRow(`${rank}. ${nickname}`, `${score} pts`)));
    }
  }
}

function render() {
  if (!state.session) showScreen("entry");
  else if (state.session.gameId) renderGame();
  else renderLobby();
  renderStatus();
}

async function action(button, work) {
  if (button.dataset.busy) return;
  button.dataset.busy = "true";
  button.disabled = true;
  notify("");
  try {
    await work();
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
    delete button.dataset.busy;
    render();
  }
}

function admit(form, join) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    action(form.querySelector("button"), async () => {
      const fields = new FormData(form);
      const body = { nickname: fields.get("nickname").trim() };
      if (!body.nickname) {
        form.elements.nickname.focus();
        throw new Error("Enter a nickname.");
      }
      let path = "/api/rooms";
      if (join) {
        const code = fields.get("code").trim().toUpperCase();
        const room = await rooms(`/api/room-codes/${encodeURIComponent(code)}`);
        path += `/${encodeURIComponent(room.room_id)}/join`;
      }
      const result = await rooms(path, { method: "POST", body });
      form.reset();
      setRoom({ ...result, host: !join });
    });
  });
}

admit($("create-form"), false);
admit($("join-form"), true);

$("copy-invite").addEventListener("click", (event) => {
  const url = new URL("/", location.origin);
  url.searchParams.set("join", state.session.code);
  action(event.currentTarget, async () => {
    try {
      await navigator.clipboard.writeText(url.href);
      notify("Invite link copied.");
    } catch {
      const input = $("invite-link");
      input.hidden = false;
      input.value = url.href;
      input.focus();
      input.select();
    }
  });
});

$("start").addEventListener("click", (event) => {
  const session = state.session;
  action(event.currentTarget, async () => {
    await audio.enable(session.room_id);
    session.pendingStart ??= {
      request_id: newRequestId(),
      room_revision: state.lobby.revision,
      settings: { round_count: 5, answer_seconds: 20 },
    };
    saveSession();
    const request = session.pendingStart;
    try {
      const result = await roomFetch(session, "/start", { method: "POST", body: request });
      if (state.session === session) applyGameId(result.game_id);
    } catch (error) {
      if (error.status > 0 && error.status < 500) {
        session.pendingStart = null;
        saveSession();
      }
      if (state.session === session && !session.gameId) throw error;
    }
  });
});

$("enable-audio").addEventListener("click", (event) => {
  const session = state.session;
  action(event.currentTarget, () => audio.enable(session.room_id));
});

$("leave-room").addEventListener("click", (event) => {
  const session = state.session;
  action(event.currentTarget, async () => {
    await roomFetch(session, "/leave", { method: "POST", body: { request_id: newRequestId() } });
    setRoom(null);
  });
});

$("leave-game").addEventListener("click", (event) => {
  const session = state.session;
  if (session.host && !confirm("End this game for everyone?")) return;
  action(event.currentTarget, async () => {
    const body = { request_id: newRequestId(), game_id: session.gameId };
    await roomFetch(session, "/leave", { method: "POST", body });
    if (!session.host) setRoom(null);
  });
});

$("back-to-lobby").addEventListener("click", () => {
  state.session.closedGameId = state.session.gameId;
  selectGame(null);
  $("invite-link").hidden = true;
  render();
});

$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  action(form.querySelector("button"), async () => {
    const generation = ++searchGeneration;
    const query = form.elements.query.value.trim();
    if (!query) return;
    const session = state.session;
    const gameId = encodeURIComponent(session.gameId);
    const results = await roomFetch(session, `/games/${gameId}/songs?${new URLSearchParams({ q: query })}`);
    if (state.session !== session || generation !== searchGeneration) return;
    const rows = results.songs.map(({ title, artist, selection }) => {
      const label = choice("radio", "song", `${title} — ${artist}`, selection);
      label.firstElementChild.addEventListener("change", () => {
        selectedSong = selection;
        setText("search-status", `Selected ${title}.`);
      });
      return label;
    });
    $("songs").replaceChildren(...rows);
    $("song-results").hidden = !rows.length;
    setText("search-status", rows.length ? "Choose a song from the results." : "No songs matched your search.");
    selectedSong = null;
  });
});

$("listeners").addEventListener("change", (event) => {
  const changed = event.target;
  if (!changed.checked) return;
  if (changed.name === "nobody") {
    for (const input of $("listeners").querySelectorAll('[name="listener"]')) input.checked = false;
  } else {
    $("listeners").querySelector('[name="nobody"]').checked = false;
  }
});

$("answer-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const session = state.session;
  const round = state.game?.round;
  if (!round || state.game.phase !== "answering") return;
  action(form.querySelector("button"), async () => {
    const fields = new FormData(form);
    const listeners = fields.getAll("listener");
    if (!listeners.length && !fields.has("nobody")) throw new Error("Choose at least one listener or Nobody.");
    const body = { song_selection: selectedSong, who_player_ids: listeners };
    await gameFetch(session, session.gameId, `/rounds/${encodeURIComponent(round.id)}/answers`, { method: "POST", body });
    if (state.session === session) submittedRound = round.id;
  });
});

try {
  const config = window.REPEAT_CONFIG;
  const roomsOrigin = configuredOrigin(config?.roomsOrigin);
  rooms = api(roomsOrigin, "Rooms");
  game = api(configuredOrigin(config?.gameOrigin), "Game");
  audio = new HostAudio(roomsOrigin);
  const params = new URLSearchParams(location.search);
  const targetCode = (params.get("join") || params.get("room") || "").toUpperCase();
  const invited = params.has("join");
  const saved = readSession();
  if (!invited && saved && (!targetCode || targetCode === saved.code)) setRoom(saved);
  else {
    if (targetCode) $("join-code").value = targetCode;
    $("create-form").hidden = invited;
    $("entry-options").classList.toggle("join-only", invited);
    showScreen("entry");
  }
  poll(pollRooms);
  poll(pollGame);
} catch {
  notify("The game services are configured incorrectly. Check the startup instructions.");
  $("entry-options").inert = true;
}
