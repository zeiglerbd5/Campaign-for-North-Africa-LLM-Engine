"""
CNA Engine — Combat Resolution Tests + Mini Scenario
Tests the three CRT systems and runs a small tactical scenario.
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cna_engine.engine.combat import (
    roll_d66, d66_in_range, resolve_barrage, resolve_anti_armor,
    resolve_close_assault,
)


def test_dice_system():
    print("=" * 60)
    print("TEST 1: Dice System")
    print("=" * 60)

    # d66 range checks
    assert d66_in_range(11, "11-53") == True
    assert d66_in_range(53, "11-53") == True
    assert d66_in_range(54, "11-53") == False
    assert d66_in_range(66, "66") == True
    assert d66_in_range(65, "66") == False
    assert d66_in_range(33, "-") == False
    assert d66_in_range(33, "") == False
    assert d66_in_range(45, "42-65") == True

    # Verify d66 distribution
    rolls = [roll_d66() for _ in range(10000)]
    valid_vals = {i * 10 + j for i in range(1, 7) for j in range(1, 7)}
    for r in rolls:
        assert r in valid_vals, f"Invalid d66 roll: {r}"

    print("  ✓ d66_in_range works correctly")
    print("  ✓ All d66 rolls are valid (11-66, no 7/8/9/0 digits)")
    print(f"  Sample rolls: {rolls[:10]}")


def test_barrage():
    print("\n" + "=" * 60)
    print("TEST 2: Barrage CRT [12.6]")
    print("=" * 60)

    # Test: 6 barrage points vs infantry, roll 42 → should be "no effect" (range 11-41)
    r = resolve_barrage("infantry", 6, dice_roll=42)
    print(f"  {r.description}")
    assert r.result == "pinned", f"Expected pinned at 42 for 5-6 col infantry, got {r.result}"

    # Test: 6 barrage points vs infantry, roll 66 → should be "1" (range is 66 for col 5-6)
    r = resolve_barrage("infantry", 6, dice_roll=66)
    print(f"  {r.description}")
    assert r.result == "1"
    assert r.strength_points_lost == 1
    assert r.is_pinned == True

    # Test: 12 barrage points vs armor, roll 25 → should be "pinned" (23-66)
    r = resolve_barrage("armor", 12, dice_roll=25)
    print(f"  {r.description}")
    assert r.result == "pinned"

    # Test: 2 BP vs truck, any roll → always no effect (11-66)
    r = resolve_barrage("truck", 2, dice_roll=11)
    print(f"  {r.description}")
    assert r.result == "no_effect"

    # Test with terrain shift: 10 BP → column shifts left 2 → effective ~6 BP
    r = resolve_barrage("infantry", 10, terrain_shifts=-2, dice_roll=55)
    print(f"  {r.description}")
    print(f"    (10 BP with -2 shift)")

    # Run a statistical sample
    results = {"no_effect": 0, "pinned": 0, "1": 0, "2": 0}
    for _ in range(1000):
        r = resolve_barrage("infantry", 10)
        results[r.result] += 1
    print(f"\n  Statistical sample: 1000 barrages at 10 BP vs infantry:")
    for k, v in results.items():
        print(f"    {k:12s}: {v:4d} ({v/10:.1f}%)")

    print("  ✓ Barrage CRT tests passed")


def test_anti_armor():
    print("\n" + "=" * 60)
    print("TEST 3: Anti-Armor CRT [14.6]")
    print("=" * 60)

    # 5 AA pts, roll 33 → row (33,34), col 5 → should be 7
    r = resolve_anti_armor(5, is_phasing=False, dice_roll=33)
    print(f"  {r.description}")
    assert r.armor_protection_lost == 7, f"Expected 7, got {r.armor_protection_lost}"

    # 0 AA pts, roll 55 → col 0 → should be 1
    r = resolve_anti_armor(0, is_phasing=False, dice_roll=55)
    print(f"  {r.description}")
    assert r.armor_protection_lost == 1

    # 3 AA pts, roll 11 → row (11,12), col 3 → should be 1
    r = resolve_anti_armor(3, is_phasing=False, dice_roll=11)
    print(f"  {r.description}")
    assert r.armor_protection_lost == 1

    # Test phasing modifier (shifts dice down one row)
    r_nophase = resolve_anti_armor(8, is_phasing=False, dice_roll=35)
    r_phase = resolve_anti_armor(8, is_phasing=True, dice_roll=35)
    print(f"  Non-phasing: {r_nophase.description}")
    print(f"  Phasing:     {r_phase.description}")
    assert r_phase.armor_protection_lost <= r_nophase.armor_protection_lost, \
        "Phasing should reduce or equal losses"

    # Test terrain shift
    r = resolve_anti_armor(6, terrain_column_shifts=-2, is_phasing=False, dice_roll=44)
    print(f"  With terrain: {r.description}")
    assert r.anti_armor_points == 4  # 6 - 2

    # Statistical sample
    total_ap = 0
    for _ in range(1000):
        r = resolve_anti_armor(8, is_phasing=True)
        total_ap += r.armor_protection_lost
    print(f"\n  Statistical: 1000 shots at 8 AA pts (phasing), avg AP lost: {total_ap/1000:.1f}")

    print("  ✓ Anti-Armor CRT tests passed")


def test_close_assault():
    print("\n" + "=" * 60)
    print("TEST 4: Close Assault CRT [15.79]")
    print("=" * 60)

    # Even fight: 10 vs 10, diff = 0
    r = resolve_close_assault(10, 10, dice_roll=33)
    print(f"  {r.description}")
    assert r.differential == 0

    # Big attacker advantage: 20 vs 5, diff = +15
    r = resolve_close_assault(20, 5, dice_roll=33)
    print(f"  {r.description}")
    assert r.is_overrun == True or r.differential == 15

    # Big defender advantage: 5 vs 20, diff = -15
    r = resolve_close_assault(5, 20, dice_roll=33)
    print(f"  {r.description}")
    assert r.attacker_loss_percent >= 20  # Should be heavy

    # All defenders pinned [15.56]
    r = resolve_close_assault(10, 8, all_defenders_pinned=True, dice_roll=33)
    print(f"  {r.description}")
    assert r.defender_strength == 0  # Pinned = defend at 0

    # Statistical sample: 10 vs 10
    atk_losses = []
    def_losses = []
    retreats = 0
    for _ in range(1000):
        r = resolve_close_assault(10, 10)
        atk_losses.append(r.attacker_loss_percent)
        def_losses.append(r.defender_loss_percent)
        if r.defender_retreat_hexes > 0:
            retreats += 1

    print(f"\n  Statistical: 1000 assaults at 10 vs 10 (diff=0):")
    print(f"    Avg attacker loss: {sum(atk_losses)/len(atk_losses):.1f}%")
    print(f"    Avg defender loss: {sum(def_losses)/len(def_losses):.1f}%")
    print(f"    Defender retreats: {retreats} ({retreats/10:.1f}%)")

    print("  ✓ Close Assault CRT tests passed")


def run_mini_scenario():
    """
    Mini Scenario: Commonwealth probe against Italian forward position.
    Demonstrates a complete combat sequence: Barrage → Anti-Armor → Close Assault.
    """
    print("\n" + "=" * 60)
    print("MINI SCENARIO: CW Probe at Sidi Barrani")
    print("=" * 60)
    random.seed(1942)  # Reproducible

    print("""
    SITUATION: December 1940. The 7th Armoured Brigade probes Italian
    positions near Sidi Barrani. 2 RTR and 7 RTR with RHA support.

    ATTACKERS (Commonwealth):
      2 RTR:  12 armor TOE, BAR 3 (Cruiser tanks)
      7 RTR:  12 armor TOE, BAR 2 (Matildas)
      1 RHA:   6 gun TOE (25-pounders)

    DEFENDERS (Italian):
      Libyan Infantry Bn:  10 infantry TOE
      M13/40 Tank Plt:      4 armor TOE, BAR 1
      75/27 Battery:         2 gun TOE

    TERRAIN: Clear, no fortifications.
    """)

    print("─" * 60)
    print("PHASE 1: BARRAGE (1 RHA fires on Libyan Infantry)")
    print("─" * 60)
    barrage_result = resolve_barrage("infantry", barrage_points=6)
    print(f"  {barrage_result.description}")
    print()

    print("─" * 60)
    print("PHASE 2: ANTI-ARMOR (2 RTR fires on M13/40s)")
    print("─" * 60)
    # 2 RTR has 12 armor points, but anti-armor uses AT rating not armor count
    # Simplified: assume 8 effective AA points
    aa_result = resolve_anti_armor(8, is_phasing=True)
    print(f"  {aa_result.description}")
    print()

    print("  ITALIAN RETURN FIRE (75/27 + M13/40 vs 2 RTR)")
    aa_return = resolve_anti_armor(6, is_phasing=False)
    print(f"  {aa_return.description}")
    print()

    print("─" * 60)
    print("PHASE 3: CLOSE ASSAULT")
    print("─" * 60)

    # Attacker: 2 RTR (12) + 7 RTR (12) = 24 assault strength
    # Defender: Infantry (10, pinned if barrage pinned) + M13/40 (4) + 75/27 (2) = 16
    # But reduce by AA losses
    atk_str = 24
    def_str = 16 - aa_result.armor_protection_lost  # Lose AP from M13s
    def_str = max(1, def_str)

    pinned = barrage_result.is_pinned

    if pinned:
        print(f"  ⚠ Italian infantry is PINNED from barrage!")

    # If infantry pinned but not all defenders pinned, no special rule
    # (all_defenders_pinned only if ALL units are pinned)
    ca_result = resolve_close_assault(atk_str, def_str)
    print(f"  {ca_result.description}")
    print()

    # Calculate actual losses
    atk_losses = round(atk_str * ca_result.attacker_loss_percent / 100)
    def_losses = round(def_str * ca_result.defender_loss_percent / 100)

    print("─" * 60)
    print("RESULTS SUMMARY")
    print("─" * 60)
    print(f"  Barrage:     {barrage_result.result.upper()}")
    print(f"  Anti-Armor:  CW destroyed {aa_result.armor_protection_lost} Italian AP")
    print(f"               Italy destroyed {aa_return.armor_protection_lost} CW AP")
    print(f"  Assault:     CW loses ~{atk_losses} SP ({ca_result.attacker_loss_percent}%)")
    print(f"               Italy loses ~{def_losses} SP ({ca_result.defender_loss_percent}%)")
    if ca_result.defender_retreat_hexes:
        print(f"               Italy RETREATS {ca_result.defender_retreat_hexes} hex(es)")
    if ca_result.is_overrun:
        print(f"               *** OVERRUN ***")
    print()


def main():
    test_dice_system()
    test_barrage()
    test_anti_armor()
    test_close_assault()
    run_mini_scenario()

    print("=" * 60)
    print("ALL COMBAT TESTS PASSED ✓")
    print("=" * 60)


if __name__ == "__main__":
    main()
