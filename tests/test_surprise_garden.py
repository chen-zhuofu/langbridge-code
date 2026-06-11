from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "synthetic-env" / "surprise_garden.py"
SPEC = importlib.util.spec_from_file_location("surprise_garden_module", MODULE_PATH)
surprise_garden = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(surprise_garden)  # type: ignore[attr-defined]

GardenChaseGame = surprise_garden.GardenChaseGame


def test_layout_validation_remains_in_bounds():
    game = GardenChaseGame(grid_size=5, gem_count=1, monster_count=1, hearts=1, seed=7)

    blockers: set[tuple[int, int]] = set()
    gems = {game.start_pos}

    assert game._layout_is_valid(blockers, gems)


def test_game_initialization_completes():
    game = GardenChaseGame(grid_size=7, gem_count=2, monster_count=1, hearts=2, seed=11)

    assert game.state == "playing"
    assert game.gems
    assert all(game._in_bounds(gem) for gem in game.gems)


def test_default_game_has_five_monsters():
    game = GardenChaseGame()

    assert len(game.monsters) == 5
