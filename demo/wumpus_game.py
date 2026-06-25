from __future__ import annotations

import argparse
import random
from typing import Iterable, List


class WumpusGame:
    """Core logic for a tiny Hunt the Wumpus style adventure."""

    ROOM_GRAPH = {
        1: (2, 3),
        2: (1, 4, 5),
        3: (1, 5),
        4: (2, 5),
        5: (2, 3, 4),
    }
    ROOMS: List[int] = sorted(ROOM_GRAPH.keys())
    PIT_COUNT = 1
    BAT_COUNT = 1

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.player_room = 0
        self.status = "playing"
        self.last_message = ""
        self.wumpus_room = 0
        self.pit_rooms: set[int] = set()
        self.bats_room = 0
        self.reset()

    # ------------------------------------------------------------------
    # Setup and helpers
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Randomize hazards and place the player in a safe room."""
        hazard_slots = 1 + self.PIT_COUNT + self.BAT_COUNT
        hazard_rooms = self.rng.sample(self.ROOMS, hazard_slots)
        cursor = 0
        self.wumpus_room = hazard_rooms[cursor]
        cursor += 1
        self.pit_rooms = set(hazard_rooms[cursor : cursor + self.PIT_COUNT])
        cursor += self.PIT_COUNT
        bat_selection = hazard_rooms[cursor : cursor + self.BAT_COUNT]
        self.bats_room = bat_selection[0]

        unsafe = {self.wumpus_room, self.bats_room, *self.pit_rooms}
        safe_rooms = [room for room in self.ROOMS if room not in unsafe]
        if not safe_rooms:
            raise RuntimeError("No safe room available for the player start")

        self.player_room = self.rng.choice(safe_rooms)
        self.status = "playing"
        self.last_message = "Find and defeat the lurking Wumpus."
        self._resolve_current_room()

    def describe_location(self) -> str:
        tunnels = ", ".join(str(room) for room in self.available_tunnels())
        return f"You are in room {self.player_room}. Tunnels lead to {tunnels}."

    def available_tunnels(self) -> Iterable[int]:
        return self.ROOM_GRAPH[self.player_room]

    def get_hints(self) -> List[str]:
        """Return ambient clues about nearby hazards."""
        hints: List[str] = []
        adjacent = self.ROOM_GRAPH[self.player_room]
        if self.wumpus_room in adjacent:
            hints.append("You smell a Wumpus.")
        if any(room in self.pit_rooms for room in adjacent):
            hints.append("You feel a breeze.")
        if self.bats_room in adjacent:
            hints.append("You hear rustling of bats.")
        return hints

    # ------------------------------------------------------------------
    # Player actions
    # ------------------------------------------------------------------
    def move(self, target_room: int) -> str:
        """Move into a connected room and resolve hazards."""
        if self.status != "playing":
            return "The adventure has already ended."
        self._validate_target(target_room)
        self.player_room = target_room
        self._resolve_current_room()
        return self.last_message

    def shoot(self, target_room: int) -> str:
        """Shoot an arrow into an adjacent room."""
        if self.status != "playing":
            return "The adventure has already ended."
        self._validate_target(target_room)
        if target_room == self.wumpus_room:
            self.status = "won"
            self.last_message = "Your arrow strikes the Wumpus. You win!"
        else:
            self.last_message = "Your arrow misses and clatters into the dark."
        return self.last_message

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _validate_target(self, target_room: int) -> None:
        if target_room not in self.ROOM_GRAPH:
            raise ValueError(f"Room {target_room} does not exist.")
        if target_room not in self.ROOM_GRAPH[self.player_room]:
            raise ValueError(
                f"Room {target_room} is not connected to room {self.player_room}."
            )

    def _resolve_current_room(self) -> None:
        """Handle hazards in the player's current room."""
        messages: List[str] = []
        while True:
            if self.player_room == self.wumpus_room:
                self.status = "lost"
                messages.append("The Wumpus awakens and devours you!")
                break
            if self.player_room in self.pit_rooms:
                self.status = "lost"
                messages.append("You tumble into a bottomless pit.")
                break
            if self.player_room == self.bats_room:
                messages.append("Super bats grab you and drop you elsewhere!")
                self.player_room = self.rng.choice(self.ROOMS)
                continue

            messages.append(f"You are now in room {self.player_room}.")
            break

        self.last_message = " ".join(messages)


def run(argv: List[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Play a mini Hunt the Wumpus.")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for deterministic cavern layouts.",
    )
    args = parser.parse_args(argv)

    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    game = WumpusGame(rng=rng)

    print("Welcome to the Caverns of the Wumpus!")
    print("Commands: move <room>, shoot <room>, help, quit")
    player_quit = False

    while True:
        print()
        print(game.describe_location())
        for hint in game.get_hints():
            print(hint)

        try:
            raw = input("Command: ").strip()
        except EOFError:
            raw = "quit"

        if not raw:
            print("Type 'help' to see the available commands.")
            continue

        parts = raw.split()
        action = parts[0].lower()

        if action == "help":
            print(
                "Use 'move <room>' to travel, 'shoot <room>' to fire an arrow, and 'quit' to exit."
            )
            continue
        if action == "quit":
            player_quit = True
            print("You holster your bow and exit the caverns.")
            break
        if action in {"move", "shoot"}:
            if len(parts) < 2:
                print("Please supply a destination room number.")
                continue
            try:
                target = int(parts[1])
            except ValueError:
                print("Room numbers must be integers.")
                continue
            try:
                message = getattr(game, action)(target)
            except ValueError as exc:
                print(exc)
                continue
            print(message)
            if game.status != "playing":
                break
            continue

        print("Unknown command. Type 'help' for controls.")

    if player_quit:
        print("Thanks for exploring the caverns!")
    elif game.status == "won":
        print("The Wumpus is slain. You emerge victorious!")
    elif game.status == "lost":
        print("Your adventure ends here. Better luck next time!")


if __name__ == "__main__":
    run()
