"""Per-game RAM address maps and story hooks for Gen-1 Pokemon.

Red and Blue (US) share one WRAM layout. Yellow (US) shifts every address in the
0xCF00-0xD7FF block down exactly one byte, while the sprite state block (0xC1xx)
and tile buffers (0xC3xx-0xC4xx) are unchanged — verified symbol-by-symbol against
the pret/pokered and pret/pokeyellow disassemblies (e.g. wPartyCount d163→d162,
wCurMap d35e→d35d, wEventFlags d747→d746). Map IDs and species internal indices
are identical across all three games, so story data (MAP_PROGRESS, quest map
gates, species/type tables) is shared.

Mirrored in empirical-evidence/autotune/game_profile.py — keep in sync.
"""

from dataclasses import dataclass, fields, replace

# Yellow's one-byte shift applies to this WRAM window (everything the agent reads
# above it or below it — sprite state, tile buffers — is unshifted).
_SHIFT_LO, _SHIFT_HI = 0xCF00, 0xD7FF


@dataclass(frozen=True)
class GameProfile:
    name: str  # stamped into telemetry events
    label: str  # human-readable, for logs/prompts
    routes_file: str  # waypoint file under references/
    # Column of the lab Pokeball the agent walks to (interact from y=4 facing up):
    # Red/Blue have three balls at (6..8, 3) and the agent picks Charmander at x=6;
    # Yellow has a single Eevee ball at (7, 3) — interacting triggers the rival grab
    # and Oak handing over Pikachu (pokeyellow scripts/OaksLab.asm).
    lab_ball_x: int
    # Pallet Town y-coordinate where the Oak intercept fires with an empty party:
    # pokered PalletTownDefaultScript checks wYCoord == 1; pokeyellow checks == 0
    # (the player must actually step onto the north boundary row).
    oak_trigger_y: int

    # Battle
    addr_battle_type: int
    addr_enemy_hp_hi: int
    addr_enemy_hp_lo: int
    addr_enemy_max_hp_hi: int
    addr_enemy_max_hp_lo: int
    addr_enemy_level: int
    addr_enemy_species: int
    addr_enemy_type1: int
    addr_enemy_type2: int
    addr_player_hp_hi: int
    addr_player_hp_lo: int
    addr_player_max_hp_hi: int
    addr_player_max_hp_lo: int
    addr_player_level: int
    addr_player_species: int
    addr_move_1: int
    addr_move_2: int
    addr_move_3: int
    addr_move_4: int
    # NOTE: pokered.sym puts wBattleMonPP at 0xD02D; the agent has always read PP
    # starting at 0xD02C. Preserved verbatim here — flagged for a separate fix.
    addr_pp_1: int
    addr_pp_2: int
    addr_pp_3: int
    addr_pp_4: int

    # Party
    addr_party_count: int
    addr_party_species_list: int
    party_base: int

    # Overworld
    addr_map_id: int
    addr_player_x: int
    addr_player_y: int
    addr_badges: int
    addr_money_1: int
    addr_money_2: int
    addr_money_3: int

    # Flags / scripts
    addr_wd730: int  # wStatusFlags5: text/menu/simulated-joypad bits
    addr_num_signs: int  # wNumSigns: current map's sign count; (y,x) coord pairs follow at +1
    addr_wd74b: int  # wEventFlags+4: bit 5 = got Pokedex
    addr_lab_script: int  # wPalletTownCurScript — logged during the Oak's-lab intro phases
    addr_diag_script: int  # wRoute15CurScript — legacy diagnostic read, preserved verbatim
    addr_warp_flag: int  # wd736 warp/scripted-movement flags (diagnose.py)

    # Bag
    addr_bag_count: int
    addr_bag_items: int

    # Below the shifted window (NOT shifted in Yellow)
    addr_player_facing: int  # wSpritePlayerStateData1FacingDirection
    addr_text_progress: int  # tile-buffer text progress byte (diagnose.py)


RED_BLUE = GameProfile(
    name="red_blue",
    label="Red/Blue",
    routes_file="routes.json",
    lab_ball_x=6,
    oak_trigger_y=1,
    addr_battle_type=0xD057,
    addr_enemy_hp_hi=0xCFE6,
    addr_enemy_hp_lo=0xCFE7,
    addr_enemy_max_hp_hi=0xCFF4,
    addr_enemy_max_hp_lo=0xCFF5,
    addr_enemy_level=0xCFF3,
    addr_enemy_species=0xCFE5,
    addr_enemy_type1=0xCFEA,
    addr_enemy_type2=0xCFEB,
    addr_player_hp_hi=0xD015,
    addr_player_hp_lo=0xD016,
    addr_player_max_hp_hi=0xD023,
    addr_player_max_hp_lo=0xD024,
    addr_player_level=0xD022,
    addr_player_species=0xD014,
    addr_move_1=0xD01C,
    addr_move_2=0xD01D,
    addr_move_3=0xD01E,
    addr_move_4=0xD01F,
    addr_pp_1=0xD02C,
    addr_pp_2=0xD02D,
    addr_pp_3=0xD02E,
    addr_pp_4=0xD02F,
    addr_party_count=0xD163,
    addr_party_species_list=0xD164,
    party_base=0xD16B,
    addr_map_id=0xD35E,
    addr_player_x=0xD362,
    addr_player_y=0xD361,
    addr_badges=0xD356,
    addr_money_1=0xD347,
    addr_money_2=0xD348,
    addr_money_3=0xD349,
    addr_wd730=0xD730,
    addr_num_signs=0xD4B0,
    addr_wd74b=0xD74B,
    addr_lab_script=0xD5F1,
    addr_diag_script=0xD625,
    addr_warp_flag=0xD736,
    addr_bag_count=0xD31D,
    addr_bag_items=0xD31E,
    addr_player_facing=0xC109,
    addr_text_progress=0xC4F2,
)


def _shift_wram(profile: GameProfile, delta: int, **overrides) -> GameProfile:
    """Derive a profile by shifting every address inside the WRAM window by ``delta``."""
    changes = {
        f.name: getattr(profile, f.name) + delta
        for f in fields(profile)
        if isinstance(getattr(profile, f.name), int) and _SHIFT_LO <= getattr(profile, f.name) <= _SHIFT_HI
    }
    changes.update(overrides)
    return replace(profile, **changes)


YELLOW = _shift_wram(
    RED_BLUE,
    -1,
    name="yellow",
    label="Yellow",
    routes_file="routes.yellow.json",
    lab_ball_x=7,
    oak_trigger_y=0,
)


def profile_for_title(title: str) -> GameProfile:
    """Map a cartridge header title to a profile.

    Unknown titles fall back to Red/Blue, the historical default (callers may warn).
    """
    t = (title or "").upper()
    if "YELLOW" in t:
        return YELLOW
    return RED_BLUE


def detect_profile(pyboy) -> GameProfile:
    """Auto-detect the game from the loaded cartridge's header title."""
    return profile_for_title(getattr(pyboy, "cartridge_title", "") or "")
