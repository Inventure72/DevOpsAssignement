"""Game-owned persistence. No query crosses into Rooms tables."""

import json


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row(value):
    return dict(value) if value is not None else None


class GameRepository:
    room_filter = "room_id=?"
    latest_query = "SELECT * FROM games WHERE {room} ORDER BY started_at_ms DESC, rowid DESC LIMIT 1"
    roster_order = "rowid"
    current_query = "SELECT * FROM rounds WHERE game_id=? ORDER BY round_number DESC, attempt DESC LIMIT 1"
    history_query = (
        "SELECT id,status,end_reason,ended_at_ms FROM games WHERE {room} "
        "AND status IN ('completed','aborted') ORDER BY ended_at_ms DESC LIMIT 50"
    )

    def room_parameters(self, room_id):
        return (room_id,)

    def game(self, conn, game_id):
        result = row(
            conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        )
        return result if result and result["id"] == game_id else None

    def latest(self, conn, room_id):
        return row(
            conn.execute(
                self.latest_query.format(room=self.room_filter),
                self.room_parameters(room_id),
            ).fetchone()
        )

    def active(self, conn):
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM games WHERE status IN ('preparing','playing')"
            )
        ]

    def start_receipt(self, conn, room_id, request_id):
        rows = conn.execute(
            f"SELECT * FROM games WHERE {self.room_filter} AND start_request_id=?",
            (*self.room_parameters(room_id), request_id),
        ).fetchall()
        return next(
            (dict(r) for r in rows if r["start_request_id"] == request_id), None
        )

    def roster(self, conn, game_id):
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM game_players WHERE game_id=? ORDER BY {self.roster_order}",
                (game_id,),
            )
        ]

    def attempts(self, conn, game_id):
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM rounds WHERE game_id = ? ORDER BY round_number, attempt",
                (game_id,),
            )
        ]

    def current(self, conn, game_id):
        return row(
            conn.execute(
                self.current_query,
                (game_id,),
            ).fetchone()
        )

    def answers(self, conn, round_id):
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM answers WHERE round_id = ?", (round_id,)
            )
        ]

    def pending_preparation(self, conn, game_id):
        preparation = row(
            conn.execute(
                "SELECT * FROM game_round_preparations WHERE game_id=?", (game_id,)
            ).fetchone()
        )
        if preparation is not None:
            preparation["acknowledgements"] = json.loads(
                preparation.pop("acknowledgements_json")
            )
        return preparation

    def save_pending_preparation(self, conn, game_id, preparation):
        self.delete_pending_preparation(conn, game_id)
        conn.execute(
            """INSERT INTO game_round_preparations
               (game_id,id,round_number,attempt,song_key,difficulty,picked_player_id,
                readiness_generation,acknowledgements_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                game_id,
                preparation["id"],
                preparation["round_number"],
                preparation["attempt"],
                preparation["song_key"],
                preparation["difficulty"],
                preparation["picked_player_id"],
                preparation["readiness_generation"],
                encode(preparation["acknowledgements"]),
            ),
        )

    def delete_pending_preparation(self, conn, game_id):
        conn.execute("DELETE FROM game_round_preparations WHERE game_id=?", (game_id,))

    def score_answer(self, conn, round_id, player_id, points):
        conn.execute(
            "UPDATE answers SET points=? WHERE round_id=? AND player_id=?",
            (points, round_id, player_id),
        )

    def receipt(self, conn, game_id, request_id):
        rows = conn.execute(
            "SELECT * FROM game_commands WHERE game_id=? AND request_id=?",
            (game_id, request_id),
        ).fetchall()
        return next((dict(r) for r in rows if r["request_id"] == request_id), None)

    def remember(self, conn, game_id, actor, kind, payload, result, now):
        conn.execute(
            """INSERT INTO game_commands
               (game_id,request_id,actor_player_id,command_type,payload_json,result_json,accepted_at_ms)
               VALUES (?,?,?,?,?,?,?)""",
            (
                game_id,
                payload["request_id"],
                actor,
                kind,
                encode(payload),
                encode(result),
                now,
            ),
        )

    def insert(self, conn, table, values):
        # SQL identifiers come from internal constants.
        assert table in {"games", "game_players", "rounds", "answers"}
        keys = ",".join(values)
        conn.execute(
            f"INSERT INTO {table} ({keys}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def update_game(self, conn, game_id, **fields):
        self._update(conn, "games", "id", game_id, fields)
        conn.execute(
            "UPDATE games SET state_version = state_version + 1 WHERE id = ?",
            (game_id,),
        )

    def update_round(self, conn, round_id, **fields):
        self._update(conn, "rounds", "id", round_id, fields)

    def _update(self, conn, table, key, value, fields):
        conn.execute(
            f"UPDATE {table} SET {','.join(f'{k} = ?' for k in fields)} WHERE {key} = ?",
            (*fields.values(), value),
        )

    def leaderboard(self, conn, game_id):
        values = [
            dict(r)
            for r in conn.execute(
                """
            SELECT p.player_id, p.nickname, p.character_id,
                   COALESCE(SUM(CASE WHEN r.status = 'revealed' THEN a.points ELSE 0 END),0) score
            FROM game_players p LEFT JOIN answers a ON a.game_id=p.game_id AND a.player_id=p.player_id
            LEFT JOIN rounds r ON r.game_id=a.game_id AND r.id=a.round_id
            WHERE p.game_id=? GROUP BY p.game_id,p.player_id,p.nickname,p.character_id
            ORDER BY score DESC,p.nickname,p.player_id""",
                (game_id,),
            )
        ]
        previous, rank = None, 0
        for position, item in enumerate(values, 1):
            if item["score"] != previous:
                rank = position
            item["rank"] = rank
            previous = item["score"]
        return values

    def save_ranks(self, conn, game_id):
        for item in self.leaderboard(conn, game_id):
            conn.execute(
                "UPDATE game_players SET final_score=?, final_rank=? WHERE game_id=? AND player_id=?",
                (item["score"], item["rank"], game_id, item["player_id"]),
            )

    def history(self, conn, room_id):
        games = conn.execute(
            self.history_query.format(room=self.room_filter),
            self.room_parameters(room_id),
        ).fetchall()
        return [
            {**dict(g), "leaderboard": self.leaderboard(conn, g["id"])} for g in games
        ]

    def delete_room_games(self, conn, room_id):
        parameters = self.room_parameters(room_id)
        conn.execute(
            f"DELETE FROM answers WHERE game_id IN (SELECT id FROM games WHERE {self.room_filter})",
            parameters,
        )
        conn.execute(f"DELETE FROM games WHERE {self.room_filter}", parameters)


class SqlServerGameRepository(GameRepository):
    room_filter = "room_id=? AND room_id_bytes=?"
    latest_query = "SELECT TOP (1) * FROM games WHERE {room} ORDER BY started_at_ms DESC, storage_order DESC"
    roster_order = "storage_order"
    current_query = "SELECT TOP (1) * FROM rounds WHERE game_id=? ORDER BY round_number DESC, attempt DESC"
    history_query = (
        "SELECT TOP (50) id,status,end_reason,ended_at_ms FROM games WHERE {room} "
        "AND status IN ('completed','aborted') ORDER BY ended_at_ms DESC,storage_order"
    )

    def room_parameters(self, room_id):
        return (room_id, len(room_id.encode("utf-16-le")))
