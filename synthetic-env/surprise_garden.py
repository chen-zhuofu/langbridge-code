"""Terminal-friendly ASCII mini garden chase game.

Run directly with:
    python synthetic-env/surprise_garden.py
"""
from __future__ import annotations

from collections import deque
import random
from typing import List, Tuple

Coordinate = Tuple[int, int]


class GardenChaseGame:
    """Simple collectible-and-escape adventure on a square ASCII grid."""

    MOVE_DELTAS = {
        "W": (-1, 0),
        "A": (0, -1),
        "S": (1, 0),
        "D": (0, 1),
    }

    def __init__(
        self,
        grid_size: int = 9,
        gem_count: int = 5,
        monster_count: int = 2,
        hearts: int = 3,
        seed: int | None = None,
    ) -> None:
        if grid_size < 5:
            raise ValueError("grid_size should be at least 5 for a playable area")
        if gem_count < 1:
            raise ValueError("gem_count must be positive")
        if monster_count < 1:
            raise ValueError("monster_count must be positive")
        if hearts < 1:
            raise ValueError("hearts must be positive")

        self.grid_size = grid_size
        self.gem_count = gem_count
        self.monster_count = monster_count
        self.max_hearts = hearts
        self._seed = seed
        self._rng = random.Random(seed)

        self.blockers: set[Coordinate] = set()
        self.gems: set[Coordinate] = set()
        self.monsters: List[Coordinate] = []
        self.player_pos: Coordinate = (0, 0)
        self.start_pos: Coordinate = (0, 0)
        self.exit_pos: Coordinate = (0, 0)
        self.remaining_hearts = hearts
        self.state = "playing"
        self.last_status = ""
        self.turn_count = 0

        self.reset()

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Reset the world layout and player state."""
        self.start_pos = (self.grid_size // 2, self.grid_size // 2)
        self.player_pos = self.start_pos
        self.exit_pos = (self.grid_size - 1, self.grid_size - 1)
        self.remaining_hearts = self.max_hearts
        self.state = "playing"
        self.turn_count = 0
        self.last_status = "Collect every gem (*) and reach the exit E."

        blockers, gems, monsters = self._generate_layout()
        self.blockers = blockers
        self.gems = gems
        self.monsters = monsters

    # ------------------------------------------------------------------
    # Core game loop pieces
    # ------------------------------------------------------------------
    def render(self) -> str:
        """Return the grid along with vital stats as a string."""
        grid = [["." for _ in range(self.grid_size)] for _ in range(self.grid_size)]

        for r, c in self.blockers:
            grid[r][c] = "#"
        for r, c in self.gems:
            grid[r][c] = "*"
        for r, c in self.monsters:
            grid[r][c] = "M"

        er, ec = self.exit_pos
        grid[er][ec] = "E"

        pr, pc = self.player_pos
        grid[pr][pc] = "@"

        rows = ["Garden Chase"]
        rows.extend("".join(row) for row in grid)
        rows.append("")
        rows.append(
            f"Hearts: {self.remaining_hearts} | Gems remaining: {len(self.gems)}"
        )
        rows.append("Controls: W/A/S/D to move, Q to quit")
        rows.append(f"Status: {self.last_status}")
        return "\n".join(rows)

    def step(self, command: str) -> str:
        """Apply a single command and return a status string."""
        if not command:
            message = "Enter W/A/S/D to move or Q to quit."
            self.last_status = message
            return message

        if self.state != "playing":
            message = "Game already concluded. Reset to play again."
            self.last_status = message
            return message

        trimmed = command.strip().upper()
        if not trimmed:
            message = "Enter W/A/S/D to move or Q to quit."
            self.last_status = message
            return message

        if trimmed == "Q":
            self.state = "quit"
            message = "You decide to leave the garden for now."
            self.last_status = message
            return message

        move = self.MOVE_DELTAS.get(trimmed)
        if move is None:
            message = "Unknown command. Use W/A/S/D to move or Q to quit."
            self.last_status = message
            return message

        target = (self.player_pos[0] + move[0], self.player_pos[1] + move[1])
        if not self._in_bounds(target):
            message = "You can't wander beyond the hedges."
            self.last_status = message
            return message
        if target in self.blockers:
            message = "A hedge blocks your path."
            self.last_status = message
            return message

        self.player_pos = target
        self.turn_count += 1

        events: List[str] = []
        if self.player_pos in self.gems:
            self.gems.remove(self.player_pos)
            events.append("You pocket a shimmering gem!")

        if not self.gems and self.player_pos == self.exit_pos:
            self.state = "won"
            events.append("You escape with every gem. Well done!")
            message = " ".join(events)
            self.last_status = message
            return message

        collision_happened = self._advance_monsters()
        if collision_happened:
            self.remaining_hearts -= 1
            if self.remaining_hearts <= 0:
                self.state = "lost"
                events.append("A monster catches you. You run out of hearts.")
                message = " ".join(events)
                self.last_status = message
                return message

            self.player_pos = self.start_pos
            events.append(
                "A monster slams into you! You lose a heart and respawn at the fountain."
            )

        if not self.gems and self.player_pos == self.exit_pos:
            self.state = "won"
            events.append("You escape with every gem. Well done!")
        elif not events:
            events.append("You creep deeper into the garden...")

        message = " ".join(events)
        self.last_status = message
        if self.state == "won":
            return message
        return message

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _advance_monsters(self) -> bool:
        """Move monsters toward the player. Return True if they collide."""
        new_positions: List[Coordinate] = []
        for monster in self.monsters:
            new_positions.append(self._next_monster_step(monster, self.player_pos))
        self.monsters = new_positions
        return any(pos == self.player_pos for pos in self.monsters)

    def _next_monster_step(self, monster: Coordinate, target: Coordinate) -> Coordinate:
        mr, mc = monster
        pr, pc = target
        row_delta = 0
        col_delta = 0
        if pr < mr:
            row_delta = -1
        elif pr > mr:
            row_delta = 1
        if pc < mc:
            col_delta = -1
        elif pc > mc:
            col_delta = 1

        options: List[Coordinate] = []
        if abs(pr - mr) >= abs(pc - mc):
            if row_delta:
                options.append((mr + row_delta, mc))
            if col_delta:
                options.append((mr, mc + col_delta))
        else:
            if col_delta:
                options.append((mr, mc + col_delta))
            if row_delta:
                options.append((mr + row_delta, mc))
        options.append((mr, mc))

        for cand in options:
            if self._in_bounds(cand) and cand not in self.blockers:
                return cand
        return monster

    def _generate_layout(self) -> Tuple[set[Coordinate], set[Coordinate], List[Coordinate]]:
        available_cells = [
            (r, c)
            for r in range(self.grid_size)
            for c in range(self.grid_size)
            if (r, c) not in {self.start_pos, self.exit_pos}
        ]

        min_required_empty = self.gem_count + self.monster_count
        if len(available_cells) < min_required_empty:
            raise ValueError("Grid is too small for the requested entities")

        blocker_budget = min(
            max(1, self.grid_size // 2),
            max(0, len(available_cells) - min_required_empty),
        )

        for _ in range(500):
            blockers = set(self._rng.sample(available_cells, blocker_budget)) if blocker_budget else set()
            open_cells = [cell for cell in available_cells if cell not in blockers]
            if len(open_cells) < min_required_empty:
                continue

            gems = set(self._rng.sample(open_cells, self.gem_count))
            remaining = [cell for cell in open_cells if cell not in gems]
            monster_candidates = [
                cell for cell in remaining if self._manhattan(cell, self.start_pos) >= 3
            ]
            if len(monster_candidates) < self.monster_count:
                continue
            monsters = self._rng.sample(monster_candidates, self.monster_count)

            if self._layout_is_valid(blockers, gems):
                return blockers, gems, monsters

        # Fallback: no blockers, deterministic placement.
        open_cells = [
            (r, c)
            for r in range(self.grid_size)
            for c in range(self.grid_size)
            if (r, c) not in {self.start_pos, self.exit_pos}
        ]
        gems = set(open_cells[: self.gem_count])
        monsters = open_cells[self.gem_count : self.gem_count + self.monster_count]
        return set(), gems, monsters

    def _layout_is_valid(self, blockers: set[Coordinate], gems: set[Coordinate]) -> bool:
        visited = set()
        queue = deque([self.start_pos])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for neighbor in self._neighbors(node):
                if not self._in_bounds(neighbor):
                    continue
                if neighbor in blockers or neighbor in visited:
                    continue
                queue.append(neighbor)

        return self.exit_pos in visited and gems.issubset(visited)

    def _neighbors(self, cell: Coordinate) -> List[Coordinate]:
        r, c = cell
        return [
            (r - 1, c),
            (r + 1, c),
            (r, c - 1),
            (r, c + 1),
        ]

    @staticmethod
    def _manhattan(a: Coordinate, b: Coordinate) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def _in_bounds(self, cell: Coordinate) -> bool:
        r, c = cell
        return 0 <= r < self.grid_size and 0 <= c < self.grid_size


def run() -> None:
    """Launch the interactive game loop."""
    game = GardenChaseGame()
    print("Welcome to Surprise Garden Chase! Collect all gems and reach the exit.")
    try:
        while True:
            print(game.render())
            try:
                command = input("Command (W/A/S/D or Q): ")
            except EOFError:
                command = "Q"
            status = game.step(command)
            print(status)
            if game.state != "playing":
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Exiting the garden.")
    finally:
        print(game.render())
        if game.state == "won":
            print("Victory! Thanks for playing.")
        elif game.state == "lost":
            print("Defeat! Better luck next time.")
        elif game.state == "quit":
            print("Come back soon!")


if __name__ == "__main__":
    run()
