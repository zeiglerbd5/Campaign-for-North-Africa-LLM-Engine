# Operation Compass -- Full 111-Turn Game

**Model:** Qwen3-8B-4bit (MLX, M4 Pro)
**Duration:** 493 minutes (~8h 13m wall-clock, ~4.4 min/turn avg)
**Date:** 2026-03-14 (started 23:34 on 2026-03-13, ended 07:47)
**Scenario:** Operation Compass, December 1940 -- February 1941
**Map:** 6,565-hex full North Africa map (sections A--E, Tripoli to Alexandria)

---

## Overview

Two instances of Qwen3-8B played both the Western Desert Force (Allied) and the Regio Esercito/DAK (Axis) across all 111 game turns of the Operation Compass scenario. The game ran overnight as a fully autonomous session -- no human intervention at any point. The Allied AI executed a historically plausible campaign: an initial fighting retreat from the Axis opening assault around Sidi Barrani, a counter-offensive that pushed the Italians west, a prolonged attritional stalemate in the desert interior driven by the water crisis, and a late-game Allied breakout that reached Tobruk by game's end. The Axis AI received DAK reinforcements around GT15-21 and launched periodic counter-attacks but could never sustain an advance due to critical supply shortages. Water dominated every decision from GT20 onward.

---

## Turn-by-Turn Highlights

### GT1--GT10: Opening Blows at Sidi Barrani
**Avg turn time: 11.6 min | 695s per turn**

The game opens with both sides in contact around hex D0821 (Sidi Barrani) and D0921. The Axis AI immediately classifies the situation as ATTACK_PREPARED and launches coordinated assaults -- artillery barrages followed by close assaults -- into the Allied line. The Italian 1st Libyan Division HQ pushes to D0921 while the Maletti Group probes forward.

The Allied AI correctly reads the pressure as FIGHTING_RETREAT and begins pulling units back toward Mersa Matruh (D1822), issuing 71 break-contact orders in this window alone. The withdrawal is orderly: units disengage, fall back one or two hexes, then re-engage. The 7th Armoured Division HQ moves to D0921 on GT1 to screen the retreat.

In the air, the Axis holds superiority (33 AIR_SUPERIORITY_HELD classifications). The RAF scrambles Blenheims for bombing runs against Italian HQs and infantry, but semi-sighted abort rolls (requiring 4+ on a d6 for most targets) cause roughly half the sorties to fail. The Royal Navy sorties consistently from Alexandria (E1326), running fleet interdiction every turn.

By GT10, supply concerns surface: 7 SUPPLY_LOW_WATER and 7 SUPPLY_LOW_FUEL classifications. The desert is already taking its toll.

**Key orders this phase:** 77 Allied moves, 43 Axis moves, 30 barrages, 25 close assaults, 71 Allied break-contacts.

### GT11--GT20: The Axis Retreat and DAK Arrival
**Avg turn time: 13.9 min | 834s per turn (slowest phase of the game)**

The momentum flips dramatically. By GT11, the Allied AI switches to ATTACK_PREPARED (23 classifications) and begins pushing west toward Sidi Barrani. The Axis AI, now reading FIGHTING_RETREAT (22 classifications), initiates a massive withdrawal. GT13 sees the Axis execute nothing but break_contact orders across all three op stages -- a full-speed retreat.

Italian units stream toward Mersa Matruh (D1822) and beyond. The Maletti Group HQ falls back to Benghazi (B1403) by GT17. The Italian 1st Libyan Artillery retreats all the way to the Tripolitanian border (A0132) by GT15 -- a 100+ hex journey that effectively removes it from the war.

**DAK arrives:** The Afrika Korps reconnaissance battalion appears at GT15, followed by 15th Panzer (two battalions) at GT18 and 5th Light Division (two battalions) at GT21. They deploy forward toward the front but immediately face the same water crisis that is strangling the Italians.

Supply becomes critical: 24 SUPPLY_CRITICAL_WATER classifications this phase, up from zero in GT1-10. Both sides begin dedicating convoy phases to emergency water draws.

Turn times peak here because both sides are simultaneously issuing complex retreat/advance orders with supply operations. GT12 is the single slowest turn at 1,087 seconds (18.1 minutes).

**Key orders this phase:** 70 Allied moves, 86 Axis moves, 41 barrages, 19 close assaults, 152 break-contacts (42 Allied, 110 Axis).

### GT21--GT30: Allied Advance, Axis Digs In
**Avg turn time: 5.4 min**

The Allied push continues westward. Units reach D0324 (near Sollum) and D0422 by GT24. The 6th Australian battalions enter the line and push aggressively. The Allied AI classifies 8 ADVANCE_OPPORTUNITY and 10 ATTACK_PREPARED situations.

The Axis consolidates. The Italian 2nd Libyan Division falls back to Mersa Matruh; the DAK 5th Light sets up around Tobruk (C0512). The Axis AI reads FIGHTING_RETREAT 10 times and DEFENSIVE_HOLD 17 times -- a force that has given up trying to attack and is focused on survival.

Water crisis deepens: 33 SUPPLY_CRITICAL_WATER. Both sides are now spending significant portions of their turns on supply draws rather than combat.

### GT31--GT40: The Siege of Sidi Barrani
**Avg turn time: 5.2 min**

The front stabilizes. Allied units hold Sidi Barrani (D0821) and mount repeated attacks against Axis positions. The Allied AI is in full ATTACK_PREPARED mode (22 classifications), launching 21 fire barrages and 17 close assaults -- the heaviest sustained bombardment of the war.

But Allied movement collapses to just 3 move_unit orders in 10 turns. The army has outrun its supply lines and is rooted in place, pouring shells into Axis positions it cannot advance past. The Axis reads DEFENSIVE_HOLD 28 times and simply absorbs the punishment.

Supply is now the dominant concern: 40 SUPPLY_CRITICAL_WATER. The water death spiral is in full effect for the Axis, and the Allies are not far behind.

### GT41--GT50: The Long Stalemate Begins
**Avg turn time: 2.1 min**

Both sides shift to DEFENSIVE_HOLD (28 Allied, 27 Axis). Combat drops to a fraction of earlier levels -- just 9 barrages and 4 close assaults total. Movement is limited and mostly concerned with supply relay.

SUPPLY_CRITICAL_WATER hits 54 classifications -- the crisis is now permanent. Every convoy phase is dedicated to emergency water draws. The DAK 5th Light 1st Battalion reaches Tobruk (C0512) at GT43, establishing a forward logistics node. The Axis recon battalion pulls back to Mersa Matruh at GT46.

Turn times drop below 2 minutes as the LLM recognizes there is little to do but maintain supply lines.

### GT51--GT60: Axis Counter-Probes
**Avg turn time: 1.7 min (fastest sustained phase)**

The Axis AI, despite critical water shortages (57 SUPPLY_CRITICAL_WATER), detects opportunities and launches limited counter-attacks. At GT54-56, Axis forces assault Allied positions around D1021-D1022 -- a modest push eastward. The 15th Panzer and 5th Light battalions execute coordinated close assaults.

The Allies respond by pulling units toward water at GT58 (classified SUPPLY_LOW_WATER), moving the bulk of their force to D0821 and D1120. This is the first time the Allied AI explicitly prioritizes water over territory.

### GT61--GT70: Axis Attacks Peak, Then Fade
**Avg turn time: 2.4 min**

The most aggressive Axis phase of the mid-game. From GT65-68, Axis forces launch a coordinated offensive around D0820-D0920, with 8 close assaults and movement toward D0720. The DAK is trying to break through.

The Allied AI responds with 14 fire barrages -- purely defensive fires to blunt the assault. Allied movement focuses on D0821 and D0920 (55 total moves), reinforcing the line. By GT68, the Axis attack exhausts itself. Both DAK 5th Light 2nd Battalion and Italian 2nd Libyan 1st Battalion pull back to Mersa Matruh.

SUPPLY_CRITICAL_WATER: 60 (every single classification). The war has become a water crisis with occasional shooting.

### GT71--GT80: Artillery Dominance
**Avg turn time: 1.6 min**

The Allied AI leans heavily into fire_barrage: 47 successful barrages in 10 turns, plus 3 anti-armor fires. This is the single most artillery-intensive phase of the entire game. Yet only 1 Allied close assault and 2 Axis close assaults occur -- nobody has the supply to actually close and take ground.

Axis movement is nearly zero: 10 move orders total (2 unique destinations: D1822 and C1714). The Axis army is effectively immobilized.

### GT81--GT90: The Quiet Before the Storm
**Avg turn time: 1.6 min**

The quietest phase of the game. The Axis AI reads DEFENSIVE_HOLD on all 30 classifications and issues almost no orders beyond supply draws. The Allied AI manages 4 close assaults and 2 barrages. Movement is limited to supply relay operations.

Both armies are exhausted, out of water, and waiting. The Axis has 4 movement orders in 10 turns.

### GT91--GT100: The Allied Breakout
**Avg turn time: 2.2 min**

Something shifts. The Allied AI detects ADVANCE_OPPORTUNITY 9 times and ATTACK_PREPARED 15 times -- the most aggressive Allied posture since GT11-20. A coordinated westward push begins:

- **GT92:** Allied close assaults hit D1223, D1323, D1423, D1524. Multiple axis positions come under simultaneous attack.
- **GT93-96:** The push continues through D1623, D0720, D0719, D0619. The Allies are fighting through Axis positions hex by hex.
- **GT97:** ADVANCE_OPPORTUNITY triggers a rapid exploitation. Allied units leap forward: C3320, C3719, C2620, D0220 -- covering dozens of hexes in a single turn. The front ruptures.
- **GT98-100:** The 7th Hussars (cw_7hus) and 11th Hussars (cw_11hus) race west, reaching C0610, C0611, then C0512 (Tobruk) by GT100. Close assaults hit C0509 (just west of Tobruk) four times.

The Axis AI detects ADVANCE_OPPORTUNITY at GT92 (moving units to D0821 and C0707) but has no strength for a counter-attack. The Italian 1st Libyan 1st Battalion is pushed to D1623.

**20 close assaults** in 10 turns -- the final Allied offensive.

### GT101--GT110: Tobruk and Beyond
**Avg turn time: 1.5 min**

The Allies consolidate around Tobruk. Units push to C0509, C0510, C0511, then beyond to C0913, C1114, C1314. The 7th Hussars reach C0509 at GT110 -- deep into Cyrenaica. The 11th Hussars hold C0611.

The Axis issues zero movement orders in this entire phase. The OVEREXTENDED_HALT classification appears for the first time at GT101-110 -- the Allied AI recognizes it has pushed to the limit of its supply lines.

One final close assault at GT106 against C0509. Then silence.

### GT111: Final Turn
**Turn time: 42.6 seconds (fastest turn of the game)**

Both sides classify DEFENSIVE_HOLD across all three op stages. The Allied 2nd RTR holds Sidi Barrani (D0821), the 3rd Hussars patrol near Derna (C1714). The Axis reads SUPPLY_CRITICAL_WATER one last time.

The war ends not with a bang but with a water shortage.

---

## Final State

### Unit Positions (Last Known)

**Allied (14 units):**
| Unit | Last Position | Hex Name |
|------|--------------|----------|
| 7th Hussars | C0509 | West of Tobruk |
| 11th Hussars | C0611 | Near Tobruk |
| 3rd Hussars | C1714 | Derna area |
| 2nd RTR | D0821 | Sidi Barrani |
| 6th Aus 1st Bn | D1122 | Interior desert |
| 6th Aus 2nd Bn | D1421 | Coastal road |
| 1st RHA | D1221 | Interior |
| RASC Trucks | D1021 | Forward supply |

**Axis (17 units):**
| Unit | Last Position | Hex Name |
|------|--------------|----------|
| DAK 15th Pz 1st Bn | C0603 | West of Tobruk |
| DAK 15th Pz 2nd Bn | C0707 | Near Tobruk |
| DAK 5th Lt 1st Bn | C0512 | Tobruk |
| DAK 5th Lt 2nd Bn | D1822 | Mersa Matruh |
| DAK Recon Bn | D1822 | Mersa Matruh |
| It. 1st Lib 1st Bn | D1623 | Coast road |
| It. 1st Lib Artillery | A0132 | Tripolitania border |
| It. Maletti HQ | B1403 | Benghazi area |
| It. M11 Platoon | A0132 | Tripolitania border |

### Supply Crisis
Water dominated the entire game from GT20 onward:
- GT1-10: 7 SUPPLY_LOW_WATER (early warning)
- GT11-20: 24 SUPPLY_CRITICAL_WATER (crisis begins)
- GT21-30: 33 SUPPLY_CRITICAL_WATER
- GT31+: 60 SUPPLY_CRITICAL_WATER per 10-turn block (every single convoy/supply classification)

The water death spiral was the defining feature of this game. Both sides spent more time managing supply draws than fighting.

---

## Statistics

### Execution
| Metric | Value |
|--------|-------|
| Total log entries | 2,943 |
| Phase entries | 2,832 |
| Turn-end entries | 111 |
| Op stages per turn | 3 (every turn) |
| Active phases (Allied) | 919 |
| Active phases (Axis) | 790 |
| Skipped phases | 1,123 |
| LLM calls | 980 |
| Total LLM time | 492 min |
| Avg LLM call duration | 30.1 sec |

### Orders Issued
| Order Type | Count |
|------------|-------|
| move_unit | 1,059 |
| draw_from_dump | 494 |
| draw_from_supply_pool | 468 |
| create_dump | 425 |
| break_contact | 316 |
| truck_load | 254 |
| fleet_sortie | 222 |
| fire_barrage | 169 |
| assign_mission (air) | 165 |
| close_assault | 117 |
| fly_sortie | 114 |
| truck_attach | 22 |
| break_engaged | 17 |
| fire_anti_armor | 10 |
| unload_port | 10 |
| recon | 7 |
| plan_convoy | 2 |
| **Total (excl. end_phase)** | **3,871** |

### Order Success Rate
| Category | Count |
|----------|-------|
| Successful | 3,509 (90.6%) |
| Failed | 362 (9.4%) |

**Failure breakdown:**
- Supply pool exhausted: 171 (47% of failures -- LLM tried to draw more than available)
- Pathfinding failures: ~80 (no valid path between hexes)
- Same-hex move: 25 (unit already at destination)
- Semi-sighted abort: 24 (air recon dice rolls)
- Aircraft not ready: 16 (maintenance/already flew)
- No enemy in target hex: 9
- Other execution errors: ~37

### Situation Classifications (Total: 2,293)
| Situation | Count | % |
|-----------|-------|---|
| PATROL_DETERMINISTIC | 618 | 26.9% |
| SUPPLY_CRITICAL_WATER | 514 | 22.4% |
| DEFENSIVE_HOLD | 448 | 19.5% |
| CONVOY_INTERDICTION | 169 | 7.4% |
| ATTACK_PREPARED | 129 | 5.6% |
| AIR_SUPERIORITY_HELD | 93 | 4.1% |
| CONVOY_DETERMINISTIC | 70 | 3.1% |
| FIGHTING_RETREAT | 49 | 2.1% |
| SUPPLY_CRITICAL_FUEL | 44 | 1.9% |
| SUPPLY_LOW_WATER | 43 | 1.9% |
| ADVANCE_OPPORTUNITY | 39 | 1.7% |
| SUPPLY_LOW_FUEL | 23 | 1.0% |
| GROUND_SUPPORT_URGENT | 22 | 1.0% |
| AIR_PARITY | 20 | 0.9% |
| AIR_INFERIORITY | 10 | 0.4% |
| OVEREXTENDED_HALT | 1 | 0.04% |

### Tactical Posture by Side
| Side | Attack | Defense | Retreat | Advance |
|------|--------|---------|---------|---------|
| Allied | 94 | 200 | 17 | 21 |
| Axis | 35 | 248 | 32 | 18 |

### Combat Totals
| Category | Allied | Axis |
|----------|--------|------|
| Close assaults (success) | 85 | 28 |
| Fire barrages (success) | 124 | 36 |
| Anti-armor fires | 8 | 1 |

### Air Operations
- Total fly_sortie (success): 76
- Total assign_mission (success): 156
- Mission types: bombing 50, strafing 22, OCAP 109, DCAP 44, recon 7
- Air superiority held (GT1-40 only, then no more air phases): 93

### Turn Timing
| GT Range | Avg Turn Time | Total |
|----------|--------------|-------|
| GT1-10 | 11.6 min | 115.9 min |
| GT11-20 | 13.9 min | 138.9 min |
| GT21-30 | 5.4 min | 54.5 min |
| GT31-40 | 5.2 min | 52.3 min |
| GT41-50 | 2.1 min | 20.8 min |
| GT51-60 | 1.7 min | 17.2 min |
| GT61-70 | 2.4 min | 24.4 min |
| GT71-80 | 1.6 min | 16.0 min |
| GT81-90 | 1.6 min | 16.3 min |
| GT91-100 | 2.2 min | 22.1 min |
| GT101-110 | 1.5 min | 14.7 min |
| GT111 | 0.7 min | 0.7 min |

Slowest single turn: GT4 at 1,119 seconds (18.7 min).
Fastest single turn: GT111 at 42.6 seconds.

---

## Commentary

**What the AI got right:**
- The Allied fighting retreat in GT1-10 followed by a counter-offensive in GT11-20 is broadly consistent with the historical Operation Compass timeline.
- The Axis AI recognized when it was outmatched and executed organized retreats rather than fighting to destruction.
- Supply management was constant and appropriate -- both sides prioritized water draws when the situation engine flagged critical shortages.
- The late-game Allied breakout (GT91-100) showed genuine operational art: the AI transitioned from attrition to exploitation, with cavalry units racing ahead while infantry consolidated.
- Air operations were front-loaded into the first 40 turns when aircraft were available, with correct OCAP/DCAP assignments.
- Fleet interdiction ran every single turn from Alexandria -- the Royal Navy never missed a sortie.

**What was unrealistic:**
- The water crisis was too severe, locking both sides into a stalemate for 60+ turns. In the real campaign, the British coastal advance kept supply lines short. The AI's inability to maintain a coast-road supply chain (roads only exist at named cities) meant units in the interior were perpetually dehydrated.
- DAK arrived at GT15-21 but was immediately paralyzed by the same water crisis. Historically, Rommel's forces were far more aggressive upon arrival.
- The 111-turn game should have seen far more territorial change. The front barely moved from GT30-GT90.
- Air operations ceased entirely after GT40 (all aircraft in maintenance with no recovery), which is not realistic for a 111-turn campaign.

**Performance notes:**
- The first 20 turns consumed 52% of total runtime (255 min out of 493 min) due to complex multi-phase combat and retreat operations.
- From GT41 onward, turns averaged under 2.5 minutes as the situation engine correctly identified low-activity states and issued minimal orders.
- The 90.6% order success rate is strong for an 8B parameter model. Most failures were supply pool exhaustion (the LLM tried to draw more than available) rather than invalid commands.
- 980 LLM calls across 111 turns = ~8.8 calls per turn, reflecting the 3 op stages x ~3 phases per stage architecture.
