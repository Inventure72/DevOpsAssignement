import { demoMedia, newRequestId } from "./transport.mjs";

export class HostAudio {
  constructor(roomsOrigin) {
    this.roomsOrigin = roomsOrigin;
    this.selectGame(null);
  }

  get enabled() {
    return this.context?.state === "running" && Boolean(this.roomId);
  }

  async enable(roomId) {
    const Context = window.AudioContext ?? window.webkitAudioContext;
    if (!Context) throw new Error("This browser cannot play game audio.");
    this.context ??= new Context();
    await this.context.resume();
    if (this.roomId === roomId) return;

    this.releaseRoom();
    if (navigator.locks) {
      await new Promise((resolve, reject) => {
        navigator.locks.request(`repeat-audio:${roomId}`, { ifAvailable: true }, (lock) => {
          if (!lock) {
            reject(new Error("Close the other host tab before enabling audio here."));
            return;
          }
          return new Promise((release) => {
            this.release = release;
            this.roomId = roomId;
            resolve();
          });
        }).catch(reject);
      });
    } else {
      this.roomId = roomId;
    }
  }

  releaseRoom() {
    this.stop();
    this.release?.();
    this.release = null;
    this.roomId = null;
  }

  selectGame(id) {
    if (this.game?.id === id) return;
    this.stop();
    this.game?.abort.abort();
    this.game = {
      id,
      abort: new AbortController(),
      manifest: null,
      candidates: new Map(),
      running: false,
      played: new Set(),
    };
  }

  has(candidateId) {
    return Boolean(this.game.candidates.get(candidateId)?.buffer);
  }

  async prepare(view, request) {
    if (!this.enabled || this.game.running) return;
    this.selectGame(view.id);
    const game = this.game;
    const current = view.round?.audio_candidate_id;
    const checking = view.phase === "setup";
    if (!checking && (!current || this.has(current))) return;

    game.running = true;
    try {
      if (!game.manifest) {
        const manifest = await request("/audio");
        if (game.abort.signal.aborted) return;
        game.manifest = manifest.candidates;
      }
      const candidates = game.manifest.filter(
        ({ candidate_id }) => (checking && !game.candidates.get(candidate_id)?.reported) ||
          (!checking && candidate_id === current),
      );
      // Limit parallel media/decode and admission requests on small devices.
      for (let index = 0; index < candidates.length; index += 4) {
        if (game.abort.signal.aborted) return;
        const reports = await Promise.allSettled(candidates.slice(index, index + 4).map(
          (candidate) => this.check(game, candidate, checking, request),
        ));
        const failed = reports.find(({ status }) => status === "rejected");
        if (failed) throw failed.reason;
      }
    } finally {
      game.running = false;
    }
  }

  async check(game, candidate, checking, request) {
    let item = game.candidates.get(candidate.candidate_id);
    if (!item) {
      item = { request_id: newRequestId(), reported: false };
      game.candidates.set(candidate.candidate_id, item);
    }
    if (!item.buffer && (!checking || item.ok === undefined)) {
      try {
        const bytes = await demoMedia(this.roomsOrigin, candidate.preview_url, game.abort.signal);
        item.buffer = await this.context.decodeAudioData(bytes);
      } catch {
        item.buffer = null;
      }
    }
    if (!checking || game.abort.signal.aborted) return;
    item.ok ??= Boolean(item.buffer);
    try {
      await request("/preload", {
        method: "POST",
        body: { request_id: item.request_id, candidate_id: candidate.candidate_id, ok: item.ok },
      });
      item.reported = true;
    } catch (error) {
      if (["setup_closed", "check_final"].includes(error.code)) {
        item.reported = true;
        return;
      }
      throw error;
    }
  }

  play(view, estimatedServerNow) {
    const round = view.round;
    if (!round || !["countdown", "answering"].includes(view.phase)) {
      this.stop();
      return;
    }
    if (!this.enabled || !this.has(round.audio_candidate_id) || this.game.played.has(round.id)) {
      return;
    }
    this.stop();
    const buffer = this.game.candidates.get(round.audio_candidate_id).buffer;
    const progress = (estimatedServerNow - round.starts_at_ms) / 1000;
    const offset = Math.max(0, progress);
    if (offset >= buffer.duration) return;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    source.start(this.context.currentTime + Math.max(0, -progress), offset);
    source.onended = () => {
      if (this.source === source) this.source = null;
      source.disconnect();
    };
    this.game.played.add(round.id);
    this.source = source;
  }

  stop() {
    this.source?.stop();
    this.source = null;
  }
}
