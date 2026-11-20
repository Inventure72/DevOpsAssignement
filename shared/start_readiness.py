"""Validate the same frozen readiness facts before reservation and acceptance."""

from collections import Counter

from shared.errors import DomainError


def require_start_readiness(snapshot):
    minimum = snapshot.get("minimum_players", 3)
    maximum = snapshot.get("maximum_players", 10)
    if not minimum <= len(snapshot["players"]) <= maximum:
        raise DomainError("player_count", f"A game needs {minimum}–{maximum} players.")
    counts = Counter(
        player_id
        for song in snapshot["songs"]
        for player_id in {listener["player_id"] for listener in song["listeners"]}
    )
    if any(counts[player["id"]] < 10 for player in snapshot["players"]):
        raise DomainError(
            "insufficient_songs", "Every player needs at least ten songs."
        )
