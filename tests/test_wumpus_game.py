"""Unit tests for the Hunt the Wumpus mini-game."""
from __future__ import annotations

import importlib.util
import pathlib
import random
from types import ModuleType


def _load_module() -> ModuleType:
    path = pathlib.Path(__file__).resolve().parents[1] / "demo" / "wumpus_game.py"
    spec = importlib.util.spec_from_file_location("wumpus_game", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()
WumpusGame = MODULE.WumpusGame


def test_start_state_is_safe_and_deterministic() -> None:
    game = WumpusGame(rng=random.Random(0))

    assert game.status == "playing"
    assert game.player_room == 3
    assert game.wumpus_room == 4
    assert game.pit_rooms == {5}
    assert game.bats_room == 1
    assert game.player_room not in {game.wumpus_room, game.bats_room, *game.pit_rooms}


def test_moving_into_pit_results_in_loss() -> None:
    game = WumpusGame(rng=random.Random(0))

    message = game.move(5)

    assert game.status == "lost"
    assert "pit" in message.lower()
    assert game.player_room == 5


def test_bats_relocate_the_player() -> None:
    game = WumpusGame(rng=random.Random(0))

    message = game.move(1)

    assert game.status == "lost"
    assert "super bats" in message.lower()
    assert game.player_room == 5  # Relocated into the known pit for this seed.


def test_shooting_the_wumpus_wins() -> None:
    game = WumpusGame(rng=random.Random(0))
    game.player_room = 2  # Safe room adjacent to the Wumpus in this layout.

    message = game.shoot(4)

    assert game.status == "won"
    assert "win" in message.lower()
