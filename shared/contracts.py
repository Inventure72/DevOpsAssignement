"""Validated values exchanged at the private service boundary."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Artist(Body):
    artist_key: Identifier
    name: Annotated[str, Field(min_length=1, max_length=500)]
    aliases: list[str] = []


class Song(Body):
    song_key: Identifier
    isrc: str | None = None
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    artist: Annotated[str, Field(min_length=1, max_length=1000)]
    artists: Annotated[list[Artist], Field(min_length=1, max_length=20)]
    preview_url: str | None = None
    artwork_url: str | None = None


class ListeningSong(Song):
    familiarity: Literal["easy", "medium", "hard"]


class ListeningImport(Body):
    provider: Annotated[str, Field(min_length=1, max_length=32)]
    evidence: Literal["personal", "simulated"]
    account_id: str | None = None
    songs: Annotated[list[ListeningSong], Field(max_length=1000)]
    observed_songs: Annotated[list[ListeningSong], Field(max_length=1000)] = []
    decoys: Annotated[list[Song], Field(max_length=1000)] = []


class FrozenPlayer(Body):
    id: Identifier
    player_id: Identifier
    nickname: str
    character_id: str
    is_host: bool
    music_status: str
    last_seen_at_ms: int


class Listener(Body):
    player_id: Identifier
    familiarity: Literal["easy", "medium", "hard"]


class FrozenSong(Song):
    pool_kind: Literal["personal", "decoy"]
    listeners: Annotated[list[Listener], Field(max_length=10)]


class RoomSnapshot(Body):
    room_id: Identifier
    revision: Annotated[int, Field(ge=0)]
    mode: Literal["demo", "normal"]
    minimum_players: Annotated[int, Field(ge=2, le=10)]
    maximum_players: Annotated[int, Field(ge=2, le=10)]
    playtest: bool
    host_id: Identifier
    players: Annotated[list[FrozenPlayer], Field(min_length=1, max_length=10)]
    songs: Annotated[list[FrozenSong], Field(max_length=1000)]

    @model_validator(mode="after")
    def identities(self):
        ids = {p.id for p in self.players}
        if len(ids) != len(self.players) or any(
            p.id != p.player_id for p in self.players
        ):
            raise ValueError("Frozen player identities must be unique and consistent")
        if [p.id for p in self.players if p.is_host] != [self.host_id]:
            raise ValueError("Snapshot must contain its single host")
        if self.minimum_players > self.maximum_players:
            raise ValueError("Invalid player limits")
        keys = {s.song_key for s in self.songs}
        if len(keys) != len(self.songs):
            raise ValueError("Frozen song identities must be unique")
        for song in self.songs:
            listeners = [listener.player_id for listener in song.listeners]
            if not song.preview_url or len(set(listeners)) != len(listeners):
                raise ValueError(
                    "Frozen songs need playable media and unique listeners"
                )
            if not set(listeners) <= ids:
                raise ValueError("Frozen listeners must belong to the roster")
        return self
