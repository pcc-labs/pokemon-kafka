"""A persistent occupancy map the agent builds as it explores — so navigation isn't blind.

The agent only ever sees a 9x10 collision window around itself and used to forget it the instant
it moved, so every wall had to be re-discovered by local groping (which is why it could reach a
fence but never find the gap that's off-screen on the far side of town).

``WorldMap`` fixes that: because the player's absolute map coordinates are known every turn, each
collision window is *stamped* into a per-map grid at the right place, accumulating a real map of
each location as the agent walks. ``plan_step`` then runs A* over that accumulated grid toward a
target — routing around walls it has already seen and heading into unexplored space when the goal
is off-screen (unknown tiles are treated as walkable so the search is drawn toward the goal, and
self-corrects as new walls are observed).

Pure logic, fully unit-tested. No pixels, no external map data — just the walkability the game
already exposes, remembered.
"""

from __future__ import annotations

import heapq

# Player is always at the centre of the 9x10 (rows x cols) collision window.
_PLAYER_ROW = 4
_PLAYER_COL = 4

# (name, dx, dy) — Pokemon Red overworld is y-down (up decreases y).
_DIRS = (("up", 0, -1), ("down", 0, 1), ("left", -1, 0), ("right", 1, 0))

_MAX_COORD = 255  # map-local coords fit in a byte; anything outside is off-map = blocked


class WorldMap:
    """Accumulated per-map walkability. ``cells[map_id][(x, y)]`` is 1 (walkable) or 0 (wall)."""

    def __init__(self) -> None:
        self.cells: dict[int, dict[tuple[int, int], int]] = {}

    def observe(self, map_id: int, px: int, py: int, grid: list[list[int]]) -> None:
        """Stamp a 9x10 collision ``grid`` (player at the centre) into the map at ``(px, py)``."""
        m = self.cells.setdefault(map_id, {})
        for r in range(min(9, len(grid))):
            row = grid[r]
            for c in range(min(10, len(row))):
                gx = px + (c - _PLAYER_COL)
                gy = py + (r - _PLAYER_ROW)
                if 0 <= gx <= _MAX_COORD and 0 <= gy <= _MAX_COORD:
                    m[(gx, gy)] = 1 if row[c] else 0

    def walkable(self, map_id: int, x: int, y: int, default: int = 1) -> int:
        """Known walkability of a tile, or ``default`` (treat unknown as walkable) if unseen."""
        return self.cells.get(map_id, {}).get((x, y), default)

    def _passable(self, m: dict[tuple[int, int], int], x: int, y: int) -> bool:
        if x < 0 or y < 0 or x > _MAX_COORD or y > _MAX_COORD:
            return False
        return m.get((x, y), 1) != 0  # unknown -> passable (optimistic, draws search to the goal)

    def plan_step(self, map_id: int, px: int, py: int, tx: int, ty: int, max_nodes: int = 8000) -> str | None:
        """First step ("up"/"down"/"left"/"right") of an A* path from ``(px, py)`` to ``(tx, ty)``
        over the accumulated map. ``None`` only when already on the target. Falls back to a greedy
        step toward the goal if A* can't reach it within ``max_nodes``."""
        if (px, py) == (tx, ty):
            return None
        m = self.cells.get(map_id, {})
        start, goal = (px, py), (tx, ty)
        openh: list[tuple[int, int, tuple[int, int]]] = [(abs(px - tx) + abs(py - ty), 0, start)]
        came: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        gscore: dict[tuple[int, int], int] = {start: 0}
        nodes = 0
        reached = None
        best = start  # closest-to-goal node seen, for the unreachable fallback
        best_h = abs(px - tx) + abs(py - ty)
        while openh and nodes < max_nodes:
            _, g, cur = heapq.heappop(openh)
            nodes += 1
            if cur == goal:
                reached = goal
                break
            ch = abs(cur[0] - tx) + abs(cur[1] - ty)
            if ch < best_h:
                best_h, best = ch, cur
            cx, cy = cur
            for _name, dx, dy in _DIRS:
                nb = (cx + dx, cy + dy)
                if not self._passable(m, nb[0], nb[1]):
                    continue
                ng = g + 1
                if nb not in gscore or ng < gscore[nb]:
                    gscore[nb] = ng
                    came[nb] = cur
                    heapq.heappush(openh, (ng + abs(nb[0] - tx) + abs(nb[1] - ty), ng, nb))
        end = reached if reached is not None else best
        step = self._first_step(came, start, end)
        if step is None:
            return self._greedy(m, px, py, tx, ty)
        return self._dir(px, py, step)

    @staticmethod
    def _first_step(came, start, end):
        """The tile adjacent to ``start`` along the reconstructed path to ``end`` (or None)."""
        if end == start or end not in came:
            return None
        node = end
        while came.get(node) not in (None, start):
            node = came[node]
        return node if came.get(node) == start else None

    @staticmethod
    def _dir(px: int, py: int, nxt: tuple[int, int]) -> str | None:
        dx, dy = nxt[0] - px, nxt[1] - py
        if dx > 0:
            return "right"
        if dx < 0:
            return "left"
        if dy > 0:
            return "down"
        if dy < 0:
            return "up"
        return None

    def _greedy(self, m, px, py, tx, ty) -> str:
        """Step toward the target along an open neighbour, minimising remaining distance."""
        best_dir, best_score = "up", None
        for name, dx, dy in _DIRS:
            nx, ny = px + dx, py + dy
            if not self._passable(m, nx, ny):
                continue
            score = abs(nx - tx) + abs(ny - ty)
            if best_score is None or score < best_score:
                best_score, best_dir = score, name
        return best_dir
