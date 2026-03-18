# Campaign for North Africa — Air Game Rules
## NJH Rewrite + SPI Errata Integration
### Companion to CNA_UNIFIED_RULES.md
### Version 0.1 — February 2026

---

## DOCUMENT SCOPE

This document covers Sections 33.0–47.0 of CNA: the Air Game. The original SPI air combat system was acknowledged as deeply flawed by designer Richard Berg himself ("sucks"). NJHarman produced a comprehensive rewrite (CC BY-SA 4.0) based on actual solo and multiplayer experience. We adopt his version as our baseline, with the official SPI errata applied underneath.

All NJH Air War rules are tagged as `NJH-CHANGE` or `NJH-ADDITION` (Layer L5) unless otherwise noted. The original SPI rules remain the fallback for anything not explicitly overridden.

### Source Attribution

> Air War rules: Norman J. Harman Jr., friendorfoe.com/war/cfna/houserules/
> Licensed: Creative Commons Attribution-ShareAlike 4.0 International
> Change History: Initial 2022-04-19, updated through 2025-07-30

---

## 1. CORE CONCEPTS

### 1.1 SQUADRON DEFINITION
Layer: NJH-CHANGE
Status: ADOPTED

A **squadron** is all aircraft from the same SGSU on a particular mission. Missions are flown by squadron. Exceptions: Recon and Transport missions may be flown by individual aircraft.

> RATIONALE: Discourages micro-managing missions to reach the next column on the Air Bombardment table. Keeps air operations at a manageable decision granularity.

### 1.2 SGSU CLARIFICATION
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA [34.72]: "This is literally correct, but, for Players' benefit, not exactly true. The SGSU does represent where the grounded planes are; they do not literally represent the planes themselves."

SGSUs are air facilities where aircraft are based. The counter tracks the facility, not the planes.

### 1.3 SQUADRON CAPACITY
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA [35.23]: "The section in the rulesbook is wrong; the chart book is right. British initial squadron capacity is 12/4, not 15/5."

### 1.4 AIRCRAFT DISTRIBUTION
Layer: NJH-CHANGE
Status: ADOPTED

[34.84] Spread aircraft out more evenly but still keep them in squadrons. Example: 53× SM.79 should arrive in squadrons across multiple Turns. But 5× Z501 (small batch) all arrive in the same Turn.

### 1.5 NO SCRAMBLE STRATEGIC MISSION
Layer: NJH-CLARIFICATION
Status: ADOPTED

Per [39.5] Air Mission Summary, there is no Scramble Strategic Mission. This only affects Commonwealth aircraft on Malta.

### 1.6 CAIRO AIRFIELDS
Layer: NJH-CLARIFICATION
Status: ADOPTED

Buried in [24.78]: all hexes of Cairo are Airfields. Scenarios are inconsistent regarding Cairo (and CW off-map) airfield availability.

### 1.7 MAJOR CITY IMMUNITY
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA [37.31]: "Planes in facilities located in major cities may always fly any mission even if there is an Enemy unit adjacent. Facilities in major cities are immune to Enemy combat units moving adjacent."

---

## 2. AIRCRAFT CLASSIFICATION

### 2.1 ATTACK BOMBERS vs LEVEL BOMBERS
Layer: NJH-CHANGE
Status: ADOPTED

A critical distinction is made between Attack Bombers and Level Bombers. Level Bombers were mostly NOT used for CAS / carpet bombing troops historically.

**Attack Bombers include:**
- All dive bombers
- All fighters doing bombing or strafing missions
- Baltimore, Boston, Maryland
- Ba.88, Ca.311, Ju.88D
- Fw.200C (only vs Commonwealth Fleet)

**Level Bombers:**
- All other bombers
- Any Attack Bomber flying night missions becomes a Level Bomber

> ENGINE NOTE: This classification drives multiple downstream rules: flak exposure, sighting requirements, bombardment column shifts, friendly fire probability. Must be tracked per-mission.

### 2.2 DIVE BOMBER RESTRICTIONS
Layer: NJH-CHANGE
Status: ADOPTED

Ju87B/D are the only German dive bombers that may do [39.2] "Dual" missions.

> RATIONALE: "Because Hitler was an idiot, almost all German aircraft were designed to be dive bomb 'capable'. Few models actually trained for or dived operationally."

### 2.3 RECON-ONLY AIRCRAFT
Layer: NJH-CHANGE
Status: ADOPTED

The Ro.37bis, Ar.196, and Hs.126 may ONLY fly Recon missions.

> RATIONALE: "Theoretically they could carry bombs but operationally were just recon aircraft."

### 2.4 Fw.200C CONDOR
Layer: NJH-CHANGE
Status: ADOPTED

- Must be 100% based in Italy, Sicily, or Crete
- Add "1 or 5" (no paradrop) transport capacity
- May conduct Flak Suppression missions ONLY against Commonwealth Fleet
- May combine Flak Suppression with Bombing CW Fleet

> RATIONALE: "Known for maritime recon, anti-shipping, and transport."

### 2.5 ADDITIONAL RECON CAPABILITY
Layer: NJH-CHANGE
Status: ADOPTED

- Add Recon capability to **Baltimore** (the real "1437 RAF Strategic Recon squadron" flew them)
- Add Recon to **Blenheim IV** (some were used for recon; with sighting rules allies need more recon)
- Allies may form two (three after GT55) **Recon Squadrons** containing Spitfire VB and/or Hurricane I/IIA/IIB. Aircraft in these squadrons may only fly Transfer, Recon, and DCAP-for-Recon missions.

### 2.6 SPI ERRATA — AIRCRAFT CHARACTERISTICS
Layer: ERRATA
Status: PATCHED

> ERRATA [4.44a]: "The Fuel Consumption rating of the Gladiator is '1'."
> ERRATA [4.44b]: "On the CW Air Characteristics Chart, the Legend shows that 'F' = Reconnaissance. That should obviously be 'R' = Reconnaissance."
> ERRATA [4.44b]: "The Italian Air Characteristics chart lists an Re.2000. If I'm not mistaken, that plane appears in no scenario or reinforcement track."
> ERRATA [4.44c]: "The Bf.109E should not have 'D' capability. In addition, the Bf.110 as a fighter has a Maneuver rating of 32 only when on Night missions. Otherwise its rating is 30."
> ERRATA [40.15]: "The last number is wrong. It should be '12', as Bf109F's have a TacAir of '6'."

---

## 3. COMBAT AIR PATROL (CAP)

### 3.1 OFFENSIVE CAP (OCAP)
Layer: NJH-CHANGE
Status: ADOPTED

Interception and scramble are by squadron.

**Interception mechanic** (replaces Air ZOC):
OCAP squadrons not already in a hex with enemy aircraft must choose one hex containing enemy aircraft within **six hexes** and attempt to intercept by rolling d6 ≥ range to target hex.

- +1 DRM if target hex is ≤ ½ OCAP's range from OCAP's base
- Each squadron may make only one interception roll

Scrambled aircraft are considered to be on OCAP. They may not intercept again.

> ENGINE NOTE: This is much cleaner than the original Air ZOC system. Each OCAP squadron makes a single probabilistic check. Distance matters — closer bases intercept more reliably. This is a good decision point for the Air Commander agent.

### 3.2 DEFENSIVE CAP (DCAP)
Layer: NJH-CHANGE
Status: ADOPTED

- Only fighters on DCAP count for screen [45.32]
- All fighters on DCAP in hex are grouped together and screen ALL non-CAP aircraft in hex, even if those aircraft "flew into" hex without an escort
- Fighters on missions other than CAP are screened (i.e., treated as non-CAP)

> RATIONALE: "DCAP is only for escorting other aircraft. Fly OCAP to 'defend' a facility, fleet, etc. Fighters on other missions, Strafing for example, are too busy doing that to defend bombers."

---

## 4. AIR-TO-AIR COMBAT

### 4.1 INITIATION
Layer: NJH-CHANGE
Status: ADOPTED

Only fighters on OCAP may initiate air combat, and they **must** do so.

### 4.2 RESOLUTION SEQUENCE (PER HEX)
Layer: NJH-CHANGE
Status: ADOPTED

In each air combat hex:

1. All OCAP conduct air combat with all enemy OCAP/DCAP in hex (if any)
2. Surviving OCAP in excess of surviving enemy CAP may (and **must** if there was no enemy CAP) conduct air combat with any aircraft in hex, including fighters on non-CAP missions

### 4.3 JETTISON
Layer: NJH-CHANGE
Status: ADOPTED

A fighter may jettison its bombs or drop tanks at **any time** during air combat. This potentially provides better stats and an unparenthesized TacAir rating.

### 4.4 FIGHTER vs FIGHTER
Layer: NJH-CHANGE
Status: ADOPTED

- At most **four** fighters may gang up on one defender
- Shots are sequential, resolved in this priority order:
  1. Better pilots first
  2. Then the outnumbered fighter
  3. Then better maneuver rating
  4. Then better TacAir differential

### 4.5 FIGHTER vs NON-FIGHTER
Layer: NJH-CHANGE
Status: ADOPTED

- At most **four** fighters may gang up on one defender
- **Non-Fighter fires first** at each attacking fighter

> ENGINE NOTE: Non-fighters get a defensive shot. This makes attacking unescorted bombers non-trivial — a Wellington rear gunner can still down a fighter.

---

## 5. POST AIR-TO-AIR SEQUENCE

### 5.1 REVISED MISSION RESOLUTION ORDER
Layer: NJH-CHANGE
Status: ADOPTED

After air-to-air combat is resolved, proceed in this order:

1. Resolve flak vs aircraft flying **Recon** missions
2. Resolve **Recon** missions
3. Roll for **semi-sighted aborts**
4. Check for **friendly fire**
5. Resolve flak vs aircraft flying **non-Recon** missions
6. Resolve **non-Recon** missions

> ENGINE NOTE: Recon resolves first because its results (sighting) determine whether subsequent missions can execute or must roll for semi-sighted abort. This creates proper information dependency.

---

## 6. FLAK

### 6.1 FLAK UNIT TYPES
Layer: NJH-CHANGE
Status: ADOPTED

[46.16] "Tanks" includes any unit with AA points lacking the Flak symbol. These may use their AA points against any Attack Bomber flying Strafing or Bombing missions.

> ERRATA [46.4]: "This chart is wrong; the notes at the bottom of Case 46.3 are correct."

### 6.2 CAP EXEMPTION
Layer: NJH-CHANGE
Status: ADOPTED

Planes flying CAP are NOT subject to Flak.

### 6.3 HIGH ALTITUDE OPTION (LEVEL BOMBERS)
Layer: NJH-CHANGE
Status: ADOPTED

Level Bombers may halve their bombardment points to only be subject to **Heavy Flak**.

> RATIONALE: Flying at high altitude. This creates a meaningful tactical choice: full bombardment strength with full flak exposure, or half strength with reduced flak risk.

### 6.4 MAP FLAK
Layer: NJH-INTERPRETATION
Status: ADOPTED

On-map printed Flak (such as in Tripoli) is considered **heavy flak**. This affects Fighter Flak Suppression missions, which cannot be flown against heavy flak [40.74].

### 6.5 TARGET GROUPS
Layer: NJH-CHANGE
Status: ADOPTED

Flak is resolved by target group:
- Daytime Attack Bombers by mission (strafing + dive bombing count as ONE mission for this purpose)
- Daytime Level Bombers by mission
- Nighttime Level Bombers by mission

### 6.6 TABLE OVERFLOW
Layer: NJH-CHANGE
Status: ADOPTED

If the number of planes in a mission group cause shifts past the end of [46.3] table, apply results from the 37+ column AND, using the same die roll, also apply results from the column as if excess shifts wrapped around to beginning of table.

**Example:** 48 planes, 3 column shifts, with 33–36 flak points. A roll of 48 would inflict 4 hits: 3 from the 37+ column and 1 from the 5–8 column.

### 6.7 AMMO CONSUMPTION
Layer: NJH-CHANGE
Status: ADOPTED

Flak unit ammo consumption is per **OpStage** in which they fire, NOT per Target Group.

### 6.8 FLAK DESTRUCTION MISSIONS REMOVED
Layer: NJH-CHANGE
Status: ADOPTED

[41.33] No Flak Destruction missions. Instead, a successful [41.37] Fortification reduction also destroys 1 pure Flak TOE point (if any) in target hex. May fly that mission vs non-fort to target Flak.

> RATIONALE: "Is too easy to whack TOE Pts."

### 6.9 AIR BOMBARDMENT TABLE CORRECTION
Layer: ERRATA
Status: PATCHED

> ERRATA [41.5] (IMPORTANT): "The 'Barrage Points' row has been screwed up: 7, 8, 9, 10 have been placed over the same column. Each column should have only two numbers, thus place 9, 10 in the next column, move the rest of the numbers one column to the right, and the last column should read 21+ (not 1+). In addition, Flak Suppression should read Flak Destruction."

---

## 7. SIGHTING AND RECONNAISSANCE

### 7.1 LAND RECON RULES
Layer: NJH-CHANGE
Status: ADOPTED

> NOTE: NJHarman's recon/sighting system was designed for solo play but works well for the engine since it replaces human judgment about what's visible with structured die rolls.

**Base rules:**
- Ignore [42.25] — recon may be flown regardless of any Air Bombardment missions
- Recon may be escorted by DCAP
- Recon may be intercepted by OCAP
- Only Heavy Flak may fire at Recon Missions (Commonwealth Fleet is NOT Heavy)
- Sighting effects last throughout current OpStage
- +1 (total) to [42.27] die roll if target hex is ≤ ½ recon's range

### 7.2 RECON RESULTS BY TARGET TYPE

**Recon vs Formation:**
- Result # > 0: Formation sighted, 1st line trucks sighted
- The result number determines how many individual Bn/Coy [3.22] unit types and approximate TOE strength of each battalion is revealed, in armor → infantry → gun order
- Specifically sighted units may be targeted by air missions (rather than random roll)

**Recon vs Truck Convoy:**
- Result # > 0: Convoy sighted
- Result # > 2: Total truck count (to nearest 10) is revealed

**Recon vs Commonwealth Fleet:**
- Result # > 0: All ships sighted; counts of ship classes (BB / CA+CL / DD) revealed

**Recon vs Supply Dump:**
- Result # > 0: Dump is semi-sighted with X = 3 + result#

**Recon vs Air Facility (including Malta):**
- Result # > 0: Reveal current level of airfield
- Result # of SGSUs' aircraft base model (e.g., "bf109" not E/F/G) is revealed

### 7.3 SEMI-SIGHTING (MANDATORY FOR NON-RECON TARGETS)
Layer: NJH-CHANGE
Status: ADOPTED

Strafing and Air Bombardment targets **must** be sighted. The following target types are **always sighted** (no roll needed):
- Targets of Flak Suppression missions
- Targets in major cities
- Port/Harbor, Road, Railroad, Flak Destruction, Fortification (including city levels), Temp Repair Facility, Air Facility, Grounded Aircraft, Water Pipeline

All other targets must be sighted by recon. If not specifically sighted by recon, each squadron must roll d6 ≤ X or be **aborted** (semi-sighting check).

### 7.4 SEMI-SIGHTING X VALUES
Layer: NJH-CHANGE
Status: ADOPTED

Each unsighted target's X is calculated individually:

| Target Type | Base X |
|------------|--------|
| Formation/Truck (SP in hex) | SP in hex (0 if >12 hexes from "front line") |
| 1st Line Trucks | # trucks / 10 |
| Commonwealth Fleet | 2 (+2 if in hex with Axis unit, +1 if BB present) |
| Supply Dump | 0 (but see recon above) |

**Modifiers (cumulative):**
- +1 for each adjacent friendly-occupied hex exerting ZOC on target hex
- +1 if target is in oasis, bir, village, fortification, or on a road (i.e., not off-road)

**Example:** Hex with 5SP Division and 1SP Battalion, no modifiers. A roll of 6 would abort a squadron targeting the Division, and 2–6 would abort if targeting the Battalion.

> ENGINE NOTE: This sighting system creates a natural fog-of-war. Large formations near the front are easy to find; small units in the deep rear are nearly invisible from the air. The Air Commander agent must weigh the risk of mission abort against target value.

---

## 8. FRIENDLY FIRE

### 8.1 WHICH MISSIONS RISK FRIENDLY FIRE
Layer: NJH-ADDITION
Status: ADOPTED

The following missions conducted against hexes **adjacent to friendly Formations** risk friendly fire:
- Strafe Infantry [40.61]
- Strafe Trucks [40.62]
- Strafe Tanks [40.65]
- Bomb Personnel [41.31]
- Bomb Trucks [41.32]

Checked per mission, prior to Flak. Divebomb + strafe counts as one mission.

### 8.2 FRIENDLY FIRE CHECK
Layer: NJH-ADDITION
Status: ADOPTED

If d6 ≤ X, then that number of mission squadrons must target adjacent, randomly determined **friendly** targets of the same type. If not possible, squadron aborts instead.

**Level Bombers:**
- X = number of adjacent, friendly-occupied hexes
- +2 if target is Engaged

**Attack Bombers and Strafing:**
- X = number of adjacent, friendly-occupied hexes that contain **Engaged** Formations
- +1 if target is Engaged

> ENGINE NOTE: Friendly fire risk increases dramatically near engaged (close combat) formations. This creates a real tension for the Air Commander: CAS near the front is risky. Bombing deep targets is safer but less tactically useful.

---

## 9. STRAFING

### 9.1 GENERAL STRAFING RULES
Layer: NJH-CHANGE
Status: ADOPTED

- Terrain adjustments for Barrage also apply to Strafing
- Max two squadrons per Parent Formation, Air Facility, Convoy, Dump, Port, or Pipe in hex

### 9.2 STRAFE VS INFANTRY
- Roll for semi-sighted aborts
- Unless specifically sighted, each squadron targets random Bn/Coy/Replacements in hex
- Instead of TacAir, calculate **1 point per aircraft** (2 per if TacAir ≥ 5)

### 9.3 STRAFE VS ARMOR
- Roll for semi-sighted aborts
- Unless specifically sighted, each squadron targets random Bn/Coy/Replacements in hex
- Calculate **2 points per aircraft**

### 9.4 STRAFE VS TRUCKS (CONVOY OR 1ST LINE)
- Roll for semi-sighted aborts
- Randomly determine trucks and cargo hit
- Calculate **full TacAir**

### 9.5 STRAFE VS GROUNDED AIRCRAFT
- Always sighted
- Calculate **½(frd) TacAir**

### 9.6 STRAFE VS SUPPLY DUMPS
- Roll for semi-sighted aborts
- Calculate **½(frd) TacAir**

### 9.7 STRAFE VS PORTS
- Always sighted
- Add **½(frd) TacAir** to Bombardment points

### 9.8 STRAFE VS WATER PIPELINE
- Always sighted
- Calculate **½(frd) TacAir**

---

## 10. AIR BOMBARDMENT

### 10.1 GENERAL BOMBARDMENT RULES
Layer: NJH-CHANGE
Status: ADOPTED

- Terrain adjustments for Barrage also apply to Air Bombardment
- Squadrons assigned to the same mission type in the same hex **beyond four** halve their bombardment points
- N/A to Naval Convoy bombing

### 10.2 BOMB VS PERSONNEL
- Level Bombers apply **two column shifts left**
- Target all personnel in hex
- Roll for semi-sighted aborts

### 10.3 BOMB VS TRUCK CONVOY
- Level Bombers apply **two column shifts left**
- Roll for semi-sighted aborts

### 10.4 BOMB VS 1ST LINE TRUCKS
- **Only Attack Bombers** may bomb 1st line trucks
- Roll for semi-sighted aborts

### 10.5 BOMB VS COMMONWEALTH FLEET
- **Only Attack Bombers and Torpedo Bombers** [30.32]
- Roll for semi-sighted aborts
- Roll once for entire hex, alternate applying losses (attacker first)

### 10.6 BOMB VS SUPPLY DUMP
- Roll for semi-sighted aborts

---

## 11. STRATEGIC AIR MISSIONS

### 11.1 TIMING IN SEQUENCE OF PLAY
Layer: NJH-CHANGE (via SoP restructuring)
Status: ADOPTED

Strategic missions are resolved during the combined II/III Strategic Stage at the start of each Game Turn. Aircraft assigned to Strategic missions cannot also fly Land Support missions during that GT's OpStages.

After strategic missions resolve:
- Return all strategic mission aircraft to base (step H)
- Perform maintenance on strategic mission aircraft (step J)

### 11.2 MALTA OPERATIONS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA [44.28]: "The second two squadrons of Ju88A's, from the Malta Table, are planes that are not available during the regular course of the game. Losses to these planes are not considered."

> ERRATA [44.2]: "This is a very confusing system. The most important thing to remember is that certain planes will be used that are never used or available for any other part of the game."

### 11.3 AIRDROP RESTRICTIONS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA [42.46]: "Fuel may not be airdropped."
> ERRATA [42.47] (ADDITION): "Units that have been airdropped are considered to have used 5 CPs already."

---

## 12. PILOT MANAGEMENT

### 12.1 PILOT ARRIVAL TIMELINE
Layer: ORIGINAL (via NJH Dates)
Status: CANONICAL

| Game Turn | Event |
|-----------|-------|
| GT7 | Start rolling for Allied and Italian pilots |
| GT23 | Start rolling for German pilots |
| GT29 | Pilot Marseilles arrives |
| GT99 | Stop rolling for pilots |

### 12.2 PILOT EXPERIENCE
Layer: ORIGINAL
Status: CANONICAL

Pilots gain experience through missions and combat. Experienced pilots have better maneuver ratings and combat effectiveness. This is one of the few truly long-horizon strategic assets in the game — a veteran pilot killed is irreplaceable.

> ENGINE NOTE: Pilot management is a key strategic decision for the Air Commander agent. Risking aces on dangerous missions vs. preserving them for critical moments is a recurring dilemma. The pilot experience system also means early-war decisions have late-war consequences — exactly the kind of long-horizon planning LLMs need to learn.

---

## 13. MAINTENANCE AND READINESS

### 13.1 GENERAL MAINTENANCE
Layer: ORIGINAL
Status: CANONICAL

Aircraft require maintenance after flying missions. Maintenance is performed during the appropriate phase of the Sequence of Play. Aircraft undergoing maintenance cannot fly.

### 13.2 SCENARIO START MAINTENANCE
Layer: NJH-CHANGE
Status: ADOPTED

Ignore [59.36] and [60.32] — maintenance may be performed first OpStage after flight.

> RATIONALE: "Designer intention seemed to be, you have only these aircraft ready. Perhaps an artifact from when maintenance was done before missions?"

### 13.3 ITALIAN CAMPAIGN START BASING
Layer: NJH-CHANGE
Status: ADOPTED

Ignore [60.32] Italy/Sicily basing prohibition. Start with 8× SM.79, 2× CR.42, and 2× CR.32 twelve-plane squadrons based in Sicily. Treat as if they were assigned to Strategic Air for GT1. No refit until GT2.

> RATIONALE: "I read forum arguments about no way were there this many aircraft in Africa."

---

## 14. THEATRE SUPPORT AND BASING REQUIREMENTS

### 14.1 THEATRE SUPPORT TIMELINE
Layer: ORIGINAL (via NJH Dates)
Status: CANONICAL

| Game Turn | Requirement |
|-----------|-------------|
| GT1–GT34 | [43.1] 75% of He111, Ju88D, FW200 must be based in Italy or Sicily |
| GT35+ | [43.1] 50% must be based in Crete; 25% in Crete, Italy, or Sicily |

### 14.2 PORT OPENING
Layer: ORIGINAL (via NJH Dates)
Status: CANONICAL

GT35: [55.17] Start attempts to open Bizerta.

---

## 15. MISCELLANEOUS ERRATA

### 15.1 REMAINING SPI ERRATA FOR AIR SECTIONS

> ERRATA [40.27]: "It is possible for a mission of planes to be intercepted, have air-to-air, fly on, be attacked again with air-to-air, fly on, etc. The only requirement is that each hex be different."

> ERRATA [40.93]: "That 20C should be 'ZOC'."

> ERRATA [40.94]: "There is no effect on other Day missions."

> ERRATA [41.31]: "The sentence at the end does not, of course, apply to fortification counters. Units in fortifications with a '2' level, other than major cities, may be bombed (with proper column adjustments, 1L)."

> ERRATA [41.35]: "Round all numbers upwards."

> ERRATA [41.46]: "Both references to 41.96 should be to 41.47."

> ERRATA [41.65B]: "Recon planes may not be attacked (not 'attracted') — and only recon planes."

> ERRATA [41.65G]: "When the Axis Convoys are completed all shipments are considered landed."

> ERRATA [41.67]: "The percentage loss is applied to each and every type of cargo listed. If the Table says losses are 20%, each type of cargo loses 20%."

> ERRATA [41.92]: "The explanation of voluntary combat is in 41.91/4."

> ERRATA [42.53]: "There is a chart for this case, located in the CW Chart Booklet."

> ERRATA [43.12]: "The German bombers referred to are He111's, Ju88D's, and FW200's."

> ERRATA [44.5]: "This chart, used by the CW, is found in the Axis Booklet."

> ERRATA [45.0]: "Figure B is missing some planes, but they are unnecessary to the example. Also, the references to choosing pilots randomly (in the paragraphs below the charts) is somewhat confusing; ignore them."

---

## 16. INFORMATION REVELATION (COMBAT INTELLIGENCE)

### 16.1 WHAT AIR RECON REVEALS
Layer: NJH-CHANGE
Status: ADOPTED

See Section 7.2 above for detailed recon results by target type.

### 16.2 DUMMY TANKS AND AIR RECON
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

[16.4] Dummy tanks are reported as Tank Units during Air Recon [42.24a].

> ENGINE NOTE: Fog of war is a first-class mechanic. The C-in-C agent should be tracking what's been sighted, when sighting expires (end of OpStage), and what intelligence gaps exist. Recon mission planning is a key C-in-C/Air Commander coordination point.

---

## 17. KEY DATES AFFECTING AIR OPERATIONS

Consolidated from NJHarman's Dates to Remember (friendorfoe.com/war/cfna/dates/):

| GT | Date | Air Event |
|----|------|-----------|
| GT1 | Sept 1940 | Campaign start. No Strategic Air on GT1. |
| GT2 | | Earliest Commonwealth Fleet sortie [60.45] |
| GT7 | | Start rolling for Allied and Italian pilots [34.83] |
| GT23 | | Start rolling for German pilots [34.83] |
| GT29 | | Pilot Marseilles arrives [34.83] |
| GT31 | | German Tanks now BAR 0 [4.49] |
| GT35 | | Theatre Support basing shifts to Crete [43.1] |
| GT35 | | Start attempts to open Bizerta [55.17] |
| GT39 | | First possible 10th Light Flotilla Raid [30.4] |
| GT39 | | NJH: First possible date for Malta invasion |
| GT99 | | Stop rolling for pilots [34.83] |

---

## APPENDIX: ENGINE IMPLEMENTATION NOTES

### Decision Points for Air Commander Agent

The Air Commander agent faces these key decisions each Game Turn and OpStage:

**Per Game Turn (Strategic Stage):**
1. Which squadrons to assign Strategic vs Land Support
2. [Axis] Theatre support allocation and Malta mission planning
3. [Axis] Naval Convoy CAP assignment
4. [Allies] Malta and Naval Convoy mission assignment

**Per OpStage (Land Support Air Phase):**
1. Recon target selection (what do we need to see?)
2. OCAP deployment (where to position interceptors?)
3. DCAP escort assignment (which bombers need protection?)
4. Mission planning: target selection, aircraft allocation, Attack vs Level bomber choice
5. Pilot assignment to missions (risk aces or protect them?)

**Key Tradeoffs:**
- Strategic missions lock aircraft out of tactical use for entire GT
- Recon investment pays off in better targeting but costs sorties
- OCAP vs DCAP allocation is zero-sum within fighter pool
- Friendly fire risk near engaged formations vs. tactical urgency
- High altitude (half bombardment, heavy-flak-only) vs. low altitude (full bombardment, all flak)
- Pilot preservation vs. mission effectiveness

### State Tracking Requirements

The engine must track per aircraft:
- Current SGSU (base location)
- Mission assignment this OpStage
- Maintenance status
- Damage status
- Pilot assignment and pilot experience level

The engine must track per SGSU:
- Location (hex)
- Capacity
- Aircraft roster
- Pilot roster

The engine must track globally:
- Sighting status per hex (expires end of OpStage)
- Theatre support requirements by date
- Pilot arrival/departure schedule
- Malta operational status

---

## DOCUMENT STATUS

| Section | Coverage | Layer |
|---------|----------|-------|
| Aircraft Classification | Complete | L5 (NJH) |
| OCAP/DCAP | Complete | L5 (NJH) |
| Air-to-Air Combat | Complete | L5 (NJH) |
| Post-Air Sequence | Complete | L5 (NJH) |
| Flak | Complete | L2 + L5 |
| Sighting/Recon | Complete | L5 (NJH) |
| Friendly Fire | Complete | L5 (NJH) |
| Strafing | Complete | L5 (NJH) |
| Air Bombardment | Complete | L5 (NJH) |
| Strategic Missions | Partial (Malta details need expansion) | L1 + L2 |
| Pilot Management | Framework only | L1 |
| Maintenance | Basic | L1 + L5 |
| Air-to-Air CRT | NOT YET (need original table) | — |
| Flak Table (corrected) | NOT YET (need original + errata applied) | — |
| Air Bombardment Table (corrected) | NOT YET (need original + errata applied) | — |
| Aircraft Characteristics (corrected) | NOT YET (need full table + all errata) | — |

### NEXT STEPS
- [ ] Air-to-Air Combat Results Table (need to extract from charts booklet)
- [ ] Corrected Flak Table (apply [46.4] errata)
- [ ] Corrected Air Bombardment Table (apply [41.5] errata)
- [ ] Full aircraft characteristics table with all errata corrections applied
- [ ] Malta operations detailed rules
- [ ] Pilot experience progression system details
- [ ] Night mission rules
- [ ] Air transport mission details
