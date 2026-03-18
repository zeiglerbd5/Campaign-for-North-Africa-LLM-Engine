# Campaign for North Africa — Unified Rules Document
## For Engine Implementation & Human Reference
### Version 0.1 — February 2026

---

## DOCUMENT PURPOSE & PROVENANCE SYSTEM

This document consolidates the rules of *The Campaign for North Africa* (SPI, 1979) from three authoritative sources into a single, machine-parseable reference for game engine implementation. Every rule entry is tagged with its provenance layer so that decisions are transparent and auditable.

### Source Documents

| Layer | Tag | Source | License/Status |
|-------|-----|--------|----------------|
| L1 | `ORIGINAL` | SPI Rulebook (1979), spigames.net PDF | Out of print, freely distributed |
| L2 | `ERRATA` | Official SPI Errata, September 1979 | Published by SPI |
| L3 | `NJH-CORRECTION` | NJHarman House Rules — CORRECTION entries | CC BY-SA 4.0 |
| L3 | `NJH-CLARIFICATION` | NJHarman House Rules — CLARIFICATION entries | CC BY-SA 4.0 |
| L4 | `NJH-INTERPRETATION` | NJHarman House Rules — INTERPRETATION entries | CC BY-SA 4.0 |
| L5 | `NJH-CHANGE` | NJHarman House Rules — CHANGE entries | CC BY-SA 4.0 |
| L5 | `NJH-ADDITION` | NJHarman House Rules — ADDITION entries | CC BY-SA 4.0 |
| L6 | `ENGINE` | Our implementation decisions | Project-specific |

### Reading Convention

Each rule is presented as:

```
[RULE NUMBER] RULE TITLE
Layer: TAG
Status: CANONICAL | PATCHED | OPTIONAL | DISPUTED

Body text of the rule as it should be applied.

> NOTES: Implementation notes, cross-references, or rationale.
```

When a rule has been modified from its original form, the modification is described inline and the original text is preserved in a `> ORIGINAL:` block for reference.

### Conflict Resolution Priority

1. Official Errata (L2) overrides Original (L1) — always
2. When errata says "the chart is right, the case is wrong" — chart takes precedence
3. NJH Corrections (L3) apply where they fix demonstrable errors
4. NJH Clarifications (L3) apply where the original is genuinely ambiguous
5. NJH Interpretations (L4) are our default but flagged as such
6. NJH Changes/Additions (L5) are OPTIONAL modules — engine can toggle them
7. Engine decisions (L6) are last resort, documented with rationale

---

## PART I: GAME STRUCTURE

---

### [1.0] INTRODUCTION
Layer: ORIGINAL
Status: CANONICAL

CNA is a simulation of operations in Libya and Egypt from September 1940 through early 1943. Each hex represents approximately 8 kilometers. Each Game-Turn represents one week. Units range from companies (~100 men) to divisions (~15,000 men).

The game consists of three layered systems:
- **Land Game** (Sections 6.0–32.0): Core movement, combat, organization
- **Air Game** (Sections 33.0–47.0): Individual planes and pilots
- **Logistics Game** (Sections 48.0–58.0): Detailed supply system replacing abstract Land Game supply

These can be combined: Land only, Land+Air, Land+Logistics, or Land+Air+Logistics (the full game).

> ENGINE NOTE: We implement the full Land+Air+Logistics game. The abstract logistics rules (Section 32.0) are NOT implemented — the errata itself warns they were "never tested" and are "twice as confusing as the difficult ones."

---

### [2.0] HOW TO PLAY THE GAME
Layer: ORIGINAL
Status: CANONICAL

#### [2.3] Team Structure (Recommended)
The game is designed for 8–10 players, 4–5 per side:

| Role | Responsibility |
|------|---------------|
| **Commander-in-Chief** | Intelligence, strategic decisions, raids, dispute resolution |
| **Logistics Commander** | All supplies, port-to-dump movement, 3rd line trucks, naval convoys |
| **Rear Area Commander** | Dump-to-front supply, reinforcements, repair facilities, construction |
| **Air Commander** | All air operations, strategic and tactical missions |
| **Front-Line Commander** | All combat units, movement, combat resolution |

> ENGINE NOTE: This maps directly to our 5-agent LLM ensemble. Each agent receives the game state relevant to its role and communicates with teammates via structured message protocol.

---

### [5.0] SEQUENCE OF PLAY
Layer: ORIGINAL + ERRATA + NJH-CHANGE
Status: PATCHED — NJHarman's restructured SoP used as primary reference

The Sequence of Play is the backbone of the engine's state machine. The original SoP is scattered across multiple rule sections. NJHarman restructured it for logical flow and async play; we adopt his version with annotations.

> ORIGINAL: The original SoP is in Section 5.2 of the rulebook, with phases defined across Sections 5.1, 33.0–47.0, and 48.0–58.0. It defines: I. Initiative Determination, II. Strategic Air Planning, III. Naval Convoy, IV. Stores Expenditure, V–VII. Operation Stages I–III, VIII. Strategic Air Recovery, IX. End of Turn.

#### RESTRUCTURED SEQUENCE OF PLAY

```
═══════════════════════════════════════════════════════
GAME TURN (1 week of real time, 100 turns in campaign)
═══════════════════════════════════════════════════════

IV. STORES EXPENDITURE, EVAPORATION & SPILLAGE STAGE
    A. Apply Evaporation and Spillage
    B. Apply Stores Expenditure
    C. Apply Attrition (for lack of Stores)

II/III. STRATEGIC STAGE (combined from original II and III)
    A. Assign squadrons to Strategic or Land Support for the Turn
    B. [Allies] Resolve Naval Convoy Recon
    C. [Axis] Plan NEXT turn's Convoy
    D. [Axis] Roll for Theatre Support; secretly assign Malta missions
    E. [Axis] Secretly assign Naval Convoy CAP
       (with knowledge of which lanes got reconned)
    F. [Allies] Assign Malta and Naval Convoy missions
    G. Resolve Malta and Naval Convoy missions
    H. Return all strategic mission aircraft to base
    J. Perform maintenance on strategic mission aircraft

───────────────────────────────────────────────────────
OPERATION STAGE (×3 per Game Turn: OpStage I, II, III)
───────────────────────────────────────────────────────

  SHARED PORTION (both sides):
    A. Weather Determination Phase
    B. Organization Phase (steps in any order):
       1. Water Distribution & Apply Attrition (lack of water)
       2. Reorganize Formations
       3. Start/continue/complete construction
       4. Perform training
       5. Redistribute supplies among trucks, coastals, trains, dumps
    C. Naval Convoy Arrival Phase
    D. Commonwealth Fleet Assignment & Repair Phase
    E. Land Support Air Phase
    F. Initiative Determination & Declaration Phase

  "A" SIDE (side with initiative goes first, or Axis if tied):
    G. Reserve Designation Phase
    H. Movement & Combat Phase (repeat steps 1-4 as desired):
       0. [NJH-ADDITION] Recon by Recce
       1. Movement Segment
       2. Breakdown
       3. Combat Segment:
          a. Barrage (Artillery)
          b. Retreat Before Assault
          c. Anti-Armor Combat
          d. Close Assault
       4. Reserve Release
    I. Vehicle Repair Phase
    J/K. Convoy Movement Phase (steps in any order):
       1. Move Truck Convoys, SGSUs, Replacement Points, POWs/Guards
       2. Tow breakdowns & destroyed tanks, move Tank Delivery Squadrons
       3. Allied Train and Port shipping
       4. Axis Coastal Shipping
    L. Patrol Phase

  "B" SIDE:
    Conduct G through L.

───────────────────────────────────────────────────────
(Repeat Operation Stage for OpStage II and III)
───────────────────────────────────────────────────────

IX. END OF GAME TURN
```

> NJH-CHANGE NOTES (rationale for restructuring):
> - **Stores Expenditure moved to start of GT**: Both sides can process independently (async play). Also more logical — you assess supply status before planning operations.
> - **Strategic Air Recovery folded into Strategic Stage (step H/J)**: Original leaves strategic aircraft "out" through all OpStages, which is confusing and nobody does it in practice.
> - **Initiative rolled per OpStage, after shared portion**: NJH dislikes the fixed 3-OpStage boundary; per-OpStage initiative creates more tension. ENGINE: We implement this as OPTIONAL — can toggle between original (per-GT) and NJH (per-OpStage).
> - **Vehicle Repairs moved before Towing/Movement**: Prevents accidentally repairing vehicles that just broke down this phase.
> - **All non-combat movement combined into Convoy Movement Phase**: Cleaner tracking.
> - **Lack-of-Stores attrition moved to Stores Expenditure Stage**: Apply once per GT when expended, not mid-OpStage.

#### [5.1] THE GAME-TURN
Layer: ORIGINAL
Status: CANONICAL

Each Game-Turn represents one week. The campaign game (Section 64.0) runs 100 turns (some sources say 111 turns from September 1940 onward; scenarios vary).

Each Game-Turn contains three Operation Stages (OpStages). Each OpStage has a shared portion (weather, organization, air) and then each side takes a "portion" in initiative order.

#### [5.11] OPERATION STAGE STRUCTURE
Layer: ORIGINAL
Status: CANONICAL

Within each Operation Stage, the side with initiative ("A" side) acts first. Initiative is determined per Section 7.0.

The "A" side completes its entire portion (Reserve through Patrol) before the "B" side begins. Units from one side cannot act during the other side's portion except via Reaction (8.5) or Retreat Before Assault (13.0).

---

## PART II: CORE SYSTEMS

---

### [6.0] THE CAPABILITY POINT SYSTEM
Layer: ORIGINAL + NJH-CLARIFICATION
Status: CANONICAL

#### [6.1] HOW THE CPA SYSTEM WORKS
Layer: ORIGINAL
Status: CANONICAL

Every unit has a Capability Point Allowance (CPA) representing its organizational efficiency. CPA is spent on movement, combat actions, organization changes, and other activities. A unit's current CPA equals its base CPA adjusted by its Cohesion level.

CPA costs are cumulative within an Operation Stage. A unit cannot spend more CPA than it has available (with exceptions for non-motorized at 150%, see 8.17).

#### [6.2] COHESION
Layer: ORIGINAL
Status: CANONICAL

Cohesion is a running modifier to CPA reflecting organizational wear. It degrades when units spend CPA and recovers when units rest.

**[6.24-1]** Cohesion Recovery: A formation that expends absolutely no CPs during an OpStage recovers cohesion points.

> NJH-CHANGE (OPTIONAL): Formations suffering an Air Bombardment pin result may not regain CP per [6.24-1]. Strafing or other in-hex Air Bombardment (including bombing 1st-line trucks) does NOT prevent CP recovery. Rationale: Original rules treat air bombardment and artillery barrage differently regarding CPA cost, which bypasses [6.25.1]'s restriction about using "absolutely no CPs."

**[6.25.1]** Recovery requires that a unit uses "absolutely no CP's" during the OpStage.

**[6.26]** A unit at -17 cohesion that is assaulted surrenders automatically. A unit at -26 cohesion that has an enemy unit move adjacent surrenders. See also [15.88].

> ERRATA [15.88]: Important distinction — assault triggers surrender at -17, mere adjacency triggers at -26. "Very subtle, these designers..."

#### [6.3] CAPABILITY POINT COST SUMMARY
Layer: ORIGINAL
Status: CANONICAL

| Action | CP Cost |
|--------|---------|
| Movement | Per terrain (see 8.3) |
| Break Contact | 4 |
| Break Engaged | 6 |
| Offensive Barrage | 1 per TOE Pt |
| Defensive Barrage | 0 |
| Anti-Armor (offensive) | 2 |
| Anti-Armor (defensive) | 0 |
| Close Assault (offensive) | 3 |
| Close Assault (defensive) | 0 |
| Undergo Barrage | 3 |
| Reorganize | varies |
| Construction | varies by type |

> ENGINE NOTE: CPA is the central resource-management mechanic. Every action has a CP cost, and the Cohesion system creates a fatigue spiral — units that do too much degrade. This is where the logistics agent's work (keeping units supplied and rested) pays off in the front-line agent's combat effectiveness.

---

### [7.0] INITIATIVE
Layer: ORIGINAL
Status: CANONICAL

#### [7.1] THE MECHANICS OF INITIATIVE
Layer: ORIGINAL
Status: CANONICAL

Each side rolls a die and adds their Initiative Rating (from Chart 7.2, which varies by scenario date). Higher total has initiative and chooses to be "A" side (act first) or "B" side.

Ties: Axis is "A" side.

> NJH-CHANGE (OPTIONAL): Initiative is rolled per OpStage (after shared portion) rather than per Game Turn. Creates more uncertainty and prevents one side from planning all three OpStages knowing they act first throughout.

> ENGINE NOTE: Per-OpStage initiative is more interesting for AI training — more decision points. Recommend enabling by default.

---

### [8.0] LAND MOVEMENT
Layer: ORIGINAL + ERRATA + NJH-CLARIFICATION/INTERPRETATION
Status: PATCHED

#### [8.1] HOW TO MOVE UNITS
Layer: ORIGINAL
Status: CANONICAL

Units move by expending Capability Points per hex entered and per hexside crossed, based on terrain. Movement occurs during Movement Segments within the Movement & Combat Phase.

#### [8.17] NON-MOTORIZED MOVEMENT LIMIT
Layer: ORIGINAL + ERRATA
Status: PATCHED

Non-motorized units may voluntarily expend during their portion of an OpStage at most **150%** of their CPA.

> ERRATA: "The % in line three should be %150, not %50." This was a critical printing error — the original said 50% which would have made non-motorized units nearly immobile.

Reaction and Retreat Before Assault occur outside your portion of an OpStage and do not count against this limit.

No movement at all if Cohesion is -26 or worse.

#### [8.2] THE CONCEPT OF CONTINUAL MOVEMENT
Layer: ORIGINAL
Status: CANONICAL

CNA uses "continual movement" — units may move, stop for combat, and continue moving in the same OpStage (if they have remaining CPA). This is resolved through repeated Movement Segments within the Movement & Combat Phase.

#### [8.23] ENTERING ENEMY ZONES OF CONTROL
Layer: ORIGINAL + ERRATA
Status: PATCHED

A unit that enters an Enemy Zone of Control (EZOC) must stop movement for that Movement Segment.

> ERRATA: "The reference should be to 8.23, not 8.22."

> NJH-REMINDER: Units are in Contact (only) if under an EZOC at the **beginning** of **any** Movement Segment [8.62]. Multiple rules ([8.24], [8.61], [8.64], [13.22]) give slightly different framings; "beginning of Movement Segment" per [8.62] is the most consistent interpretation.

#### [8.37] TERRAIN EFFECTS CHART
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "Footnote (4) should be with Major City, not Swamp."
> ERRATA (IMPORTANT): "Footnote (8) is correct, not the number '1' listed on the chart. Tracks do not cost 1 CP; they simply halve the cost of the terrain they're in."

> ENGINE NOTE: This errata is critical for movement calculation. Tracks are NOT roads — they modify underlying terrain cost by 50%, not replace it.

#### [8.5] REACTION
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

Reaction allows non-phasing units to move in response to enemy movement.

**[8.52]** Last sentence states reacting unit "does not expend CPs for Breaking Contact/Engaged."
> NJH-CORRECTION: [8.53d] says units in combat or Engaged may not React at all. [8.53c] says units under EZOC may not React. The last sentence of [8.52] is therefore redundant and confusing. "Write each rule once, clearly."

**[8.53]** Units that may NOT React:
- Non-motorized units and SGSUs
- Units under EZOC [8.53c]
- Units in combat or Engaged [8.53d]
- Truck Convoys and Tank Delivery Squadrons unless stacked with a friendly combat unit [8.53a]

> NJH-INTERPRETATION: Whether "not stacked with friendly combat units" applies to the whole list or just Truck Convoys is unclear. Ruling: SGSUs and non-motorized are not agile enough to React regardless of stacking.

**Preventing Reaction:** To prevent target from Reacting, attacker needs ALL of:
- 5+ CPA more than target
- 2+ SP if target is a 5SP "Division"
- 1+ SP if target is a 2-3SP "Brigade"

#### [8.7] RAIL MOVEMENT
Layer: ORIGINAL + ERRATA + NJH-CLARIFICATION
Status: PATCHED

> ERRATA [8.71]: Rail lines may be used by the Axis Player under Section 54.4. (This was added just as the game went to press.)

> ERRATA [8.73] (ADDITION): Units travelling by railroad may not earn any reorganization points in the Stage they do so.

> NJH-CLARIFICATION: In each direction may rail units OR supplies. As [8.77] states, units and supplies may be picked up/dropped off as long as total capacity is never exceeded. "Rules are written way more complicated than needs be. [8.72] and [8.74] say largely same thing and conflict with [8.77]."

#### [8.86] REINFORCEMENT ARRIVAL
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (IMPORTANT): "This is a mistake, and is directly opposite to what it should say. Such reinforcements, etc., may move in the Stage of their arrival."

#### [8.88] TRUCKS AND MOVEMENT
Layer: ORIGINAL + NJH-CHANGE
Status: PATCHED (OPTIONAL CHANGE)

> NJH-CHANGE (OPTIONAL): Trucks may move during the OpStage if all loading/unloading is done during the Organization Phase. "Stupid otherwise."

#### [8.9] MOTORIZED UNITS / MIXED FORMATIONS
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

> NJH-INTERPRETATION: A Formation with both non-motorized and motorized units (even just 1st-line attached trucks) must pay the most expensive CP cost for each hex and hexside. The definition of "motorized" in [3.1] is ambiguous when applied to the Terrain Effects Chart — if any vehicle makes a unit motorized, then nearly every unit qualifies. Ruling: pay worst-case for mixed formations.

---

### [9.0] STACKING
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [9.16]
Layer: ERRATA
Status: PATCHED

> ERRATA: "The third paragraph should be 'c,' not 'a'."

---

### [10.0] ZONES OF CONTROL
Layer: ORIGINAL + NJH-CLARIFICATION
Status: PATCHED

#### [10.1] WHICH UNITS EXERT A ZOC
Layer: ORIGINAL
Status: CANONICAL

To exert a ZOC requires more than 1 SP AND more than 9 Raw Defensive Close Assault Points in the hex. A lone battalion does NOT exert a ZOC.

#### [10.2] EFFECTS OF ZONES OF CONTROL
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

**[10.24]** May always advance EZOC-to-EZOC into a hex vacated due to Reaction, Retreat Before Assault, or combat result (during next Movement Segment, regardless of [8.23]).

#### [10.3] ZOC COMBAT REQUIREMENTS (HOLDING OFF)
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "This Case does not apply to the non-Phasing player."

**[10.31]** Probe satisfies EZOC holding-off requirement only if **basic** differential is -4 or better.

**[10.32]** Don't need to attack hexes exerting EZOC that contain only Guns, non-combat units, and/or pinned units.

> NJH-CHANGE (OPTIONAL): [10.23] If all units exerting EZOCs React away, moving units may continue movement. "If not sticking around, then you don't impede enemy movement."

> NJH-CHANGE (OPTIONAL): [10.26] A friendly unit negating EZOCs for another unit must end the Movement Segment in the negated EZOC hex. "Prevents rapid chain of ZOC negation. Based on OCS rule."

---

### [11.0] THE COMBAT SYSTEM
Layer: ORIGINAL + ERRATA + NJH-CLARIFICATION
Status: PATCHED

#### [11.32] CALCULATION OF COMBAT STRENGTHS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "The '+' in the third line should be a 'x'." (Multiplication, not addition — critical for combat calculation.)

#### COMBAT SEQUENCE REMINDER
Layer: NJH-REMINDER
Status: CANONICAL

All hexes undergo each step of "H.3 Combat Segment" before the next step is started. This is clear from [11.0] rules and SoP but often forgotten in play. It affects multi-hex combat, Retreat Before Assault, and various tactical situations.

---

### [12.0] BARRAGE (ARTILLERY COMBAT)
Layer: ORIGINAL + ERRATA + NJH-INTERPRETATION
Status: PATCHED

#### [12.33] TERRAIN ADJUSTMENTS
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

> NJH-INTERPRETATION: It is unclear if terrain adjustments to Barrage are intended to apply to attacker. Ruling: they DO apply to the attacker. Rationale: (1) Every other wargame does it this way, (2) [14.0] specifically says either player may be the attacker and/or defender — these are not synonyms for phasing/non-phasing.

#### [12.44] TARGET IDENTIFICATION
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (IMPORTANT): "The term 'entire target unit' refers to the battalion-level equivalent fired at. Thus, if a British artillery unit fires at an infantry battalion in a German division, and the result is a Pinned, only that battalion is pinned — not the division. Artillery fire is never against the hex; it is always against specific targets (usually battalion-equivalents.)"

#### [12.54] BARRAGE VS DUMPS / AIR FACILITIES
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

Uses **raw Barrage points on the Bombload row**, NOT the Barrage Points row.

---

### [13.0] RETREAT BEFORE ASSAULT
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

#### [13.1] WHICH UNITS MAY RETREAT
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

ANY non-Phasing unit — not only those about to be Close Assaulted — that is not prohibited may Retreat Before Assault. If not adjacent to enemy, may only expend 4 CP (or enough to move one hex, whichever is greater) [13.24].

> NJH-INTERPRETATION: Retreat Before Assault does not follow the requirements of involuntary retreat [15.82]. Retreat Before Assault may be an "advance." Reading of [13.27]'s "voluntary" supports this.

---

### [14.0] ANTI-ARMOR COMBAT
Layer: ORIGINAL + ERRATA + NJH-INTERPRETATION
Status: PATCHED

#### [14.22] RESOLUTION
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

Each hex is resolved individually. Sum all Phasing side's Anti-Armor firing at target hex. Sum all non-Phasing side's Anti-Armor firing FROM target hex.

#### [14.32] TERRAIN EFFECTS
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

> NJH-INTERPRETATION: Anti-Armor shifts due to defender's terrain apply to BOTH sides. Shifts for hexsides apply ONLY to attacker. Example: Attacking through slope into mountain = 3L shifts to attacker's anti-armor fire and 2L shifts to defender's. Rationale: [14.0] says either player may be attacker or defender; [14.6] chart has entry applying only to attacker; [11.4] chart says "Terrain may benefit either Player" and "Non-phasing is benefited by enemy forces having to enter their hex" — meaning defenders don't incur hexside shifts.

#### [14.47] SP GUNS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (IMPORTANT): "This case is wrong; some over-zealous developer decided to start changing rules. SP guns use their Armor Protection Ratings the same as any other armored unit. However, if such SP Gun is barraging 'Back,' it may not be used to absorb, nor is it affected by, Anti-Armor Fire."

#### [14.48] HALFTRACK LOSSES
Layer: ERRATA (ADDITION)
Status: CANONICAL

> ERRATA: "A maximum of two TOE points of 'halftrack-motorized' units may be so lost in any given segment of Anti-Armor Fire, regardless of the situation."

#### [14.52] CAPTURING DESTROYED TANKS
Layer: ORIGINAL + NJH-CLARIFICATION
Status: PATCHED

> NJH-CLARIFICATION: A battalion-sized (1 SP of units) or larger combat unit is required to capture Destroyed Tanks. "Rule says 'Player enters a hex' — Players aren't units in the game! Similar to Broken Down vehicle capture [21.52]."

#### ANTI-ARMOR REMINDERS
Layer: NJH-REMINDER
Status: CANONICAL

- Phasing player **always** decreases his dice roll by one row (from [14.6] chart)
- Attacking down escarpment: -2L to attacker only

---

### [15.0] CLOSE ASSAULT
Layer: ORIGINAL + ERRATA + NJH-CLARIFICATION
Status: PATCHED

#### [15.24] MULTI-HEX ASSAULT LIMITS
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

Read carefully — limits multiple hexes Close Assaulting multiple defending hexes. Attacker may be required to make separate attacks (or Holding Off barrages). Remember: a unit's TOE Points may be split to assault separate hexes.

#### [15.4] COMBINED ARMS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "In the fourth from last line note that the '...Close Assault Strength would be reduced to one.'"

Minus 1 actual Close Assault Strength for every 1-3 unsupported tank TOE Points.

#### [15.53] SIZE ADJUSTMENT
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "The words 'Brigade,' now under the Adjustment column, should be under the Smaller Side column."

#### [15.55] ???
Layer: ORIGINAL + ERRATA
Status: DISPUTED

> ERRATA: "Well, not really a clarification. I assume you don't understand what this means, as I, the designer, do not. If you do understand it, fine. If not, just continue on."

> ENGINE NOTE: The designer himself doesn't understand this rule. We skip it. If someone figures it out, it can be added later.

#### [15.56] ALL DEFENDERS PINNED
Layer: ERRATA (ADDITION)
Status: CANONICAL

> ERRATA: "If all defending units in a hex are pinned and that hex is assaulted, the units defend with a strength of '0'. In addition, there is a two column shift to the right (to account for the effects of 15.51)."

#### [15.79] CASUALTY TABLE
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA: "Under defender Losses, +4 differential, 10% line, the dice-roll should read 34-45."

#### [15.81] ENGAGED STATUS
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

Engaged applies to ALL units participating in Close Assault — not just the attackers, despite being on the attacker's portion of the CRT.

- Engaged results are ignored when there is also a Retreat result [15.74].
- Engaged results are ignored when Probing [15.93].

> NJH-CHANGE (OPTIONAL): Do not remove Engaged at end of OpStage. "OpStage boundary is game artifact, why should units not remain locked in battle!?"

#### [15.82] INVOLUNTARY RETREAT
Layer: ORIGINAL + NJH-CLARIFICATION
Status: PATCHED

> NJH-CLARIFICATION: Can only withhold all units and retreat if path exists and is taken [15.29]. Cannot use [15.82] and take 10-30% losses for hexes not retreated.

#### [15.83] CASUALTIES
Layer: ORIGINAL + NJH-CLARIFICATION
Status: PATCHED

**[15.83b]** "Totals all Raw Combat Assault Strength Points involved in the assault."

> NJH-CLARIFICATION: The combat example has each side totaling their Close Assault points. Each side should total ONLY their own Close Assault points. "Interesting effect to represent stronger force inflicting more casualties. But combat differential and overrun already is doing this."

**[15.83c]** Overrun: losses are rounded UP.

**[15.84c]** Overrun: all Gun TOE Points take Vulnerability losses.

#### [15.88] SURRENDER AND COHESION
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (CLARIFICATION): "If a unit with a -17 cohesion level is assaulted, it surrenders automatically. If the same unit had an Enemy unit move adjacent to it, it would not surrender; however, a -26 unit would, in the latter instance."

---

### [16.0] PATROLS AND RECONNAISSANCE
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [16.11] WHICH UNITS MAY PATROL
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (ADDITION): "Add to your list of units which may be used for patrol: Italian L/6's, Commonwealth Stuarts, and anyone's mechanized infantry (or Panzergrenadiers)."

#### [16.4] DUMMY TANKS
Layer: ORIGINAL + NJH-REMINDER
Status: CANONICAL

Dummy tanks are reported as Armor Class during Barrage [12.22] and as Tank Units during Air Recon [42.24a].

---

### [17.0] MORALE
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [17.28]
> ERRATA: "This means that it is possible for a unit to have a final Morale of +4."

#### [17.3] TRAINING
> ERRATA: "Yes, I know that the Axis has Training Centers. However, they are used only to train Replacement Points (Cf. Case 20.43), not actual units."

---

### [19.0] ORGANIZATION AND REORGANIZATION
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [19.5] ATTACHMENT LIMITS
Layer: ORIGINAL + ERRATA
Status: PATCHED

> ERRATA (IMPORTANT CLARIFICATION): "Lord knows why but the whole idea behind all this confusion was never expressed in simple words: Parent Units may exceed their normal, assigned unit levels (19.3) by attaching (not assigning) smaller units above and beyond those stated levels. The number and types of units that may be so attached are given in this chart. These attached units are carried in addition to those normally assigned (even though some of those normally-assigned units may be somewhere else at that time!). Just follow the chart and keep track of all these additional attachments on your TOE Log Sheets."

---

### [20.0] REINFORCEMENTS, REPLACEMENTS, AND WITHDRAWALS
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [20.3] REPLACEMENT POINT CONVERSION
> ERRATA: "The last item on this chart is the SGSU. Ignore that reference; SGSU's come in as desired, as per 34.82. They do not require any Replacement Points."

#### [20.62]
> ERRATA: "Note, in the example at the end of the Case, that the Axis Player would need 300 (not 350) tons."

#### [20.72] COMMONWEALTH PRODUCTION
> ERRATA (IMPORTANT): "The first line should state that the CW must plan one month in advance (not two). This will conform this Case with other rules and tables. Moreover, the Production Tables are used for the Month/Turn in which the CW Player plants his arrivals. (The Case now states the opposite, which is wrong.)"

---

### [21.0] BREAKDOWN
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [21.12]
> ERRATA: "In the next to last line note that Italian M 13/40's have a BAR of IR, as listed on the charts."

---

### [22.0] REPAIR
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [22.6] TANK DELIVERY SQUADRONS
Layer: ORIGINAL + NJH-INTERPRETATION
Status: PATCHED

> NJH-INTERPRETATION: TDS move during Truck Convoy Movement Phase, even when not towing. The rules never state when they move if not towing. "Based on [22.63] intent was probably during Truck Convoy if not towing. Cleaner to always move during same phase."

> NJH-INTERPRETATION: Must be stacked with a combat unit to use Reaction, Retreat Before Assault, or perform a CRT-mandated Retreat. "[22.63] 'If alone in hex...' implies not restricted when not alone."

#### [22.8] REPAIR TABLE
> ERRATA: "The last two sentences, concerning additions to the dierolls, on the Table explanation are wrong. See Case 22.34 for the correct dieroll modifications."

---

### [23.0] ENGINEERS
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [23.11]
> ERRATA: "Engineers may use parenthesized strengths only if they are not stacked with a Friendly combat unit. Also, Engineers may always enter Friendly-occupied, Enemy-controlled hexes."

---

### [24.0] CONSTRUCTION
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [24.15]
> ERRATA: "Case 24.12 is an exception to the last sentence."

#### [24.72]
> ERRATA (ADDITION): "Commonwealth SGSU's and HQ's with Engineer capability may also construct Airfields and basins."

---

### [27.0] DESERT RAIDERS & COMMANDOS
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [27.16]
> ERRATA: "LRDG's, when returning, are formed as per Case 27.13."

#### [27.36]
> ERRATA: "Desert Raiders may use Reaction after any Spotting attempt."

---

### [28.0] PRISONERS
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [28.17]
> ERRATA: "The number '1' in line four should be '5'."

---

### [29.0] WEATHER
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [29.1] WEATHER DETERMINATION
> ERRATA: "Again, the old dating method. Just consider the Roman numerals to represent the week in that month. Thus Spring runs from the 3rd week of March and ends with the 2nd week of June."

#### [29.61] WEATHER TABLE
> ERRATA (IMPORTANT): "This chart, as some of you may have suspected, is completely backwards. The correct seasonal sequence is noted in 29.1."

> ENGINE NOTE: The weather table in the printed rulebook has seasons reversed. Use the corrected sequence per 29.1.

---

### [30.0] THE MEDITERRANEAN FLEET (COMMONWEALTH)
Layer: ORIGINAL + ERRATA + NJH-CORRECTION
Status: PATCHED

#### [30.15] FLEET RANGE
Layer: ORIGINAL + NJH-CORRECTION
Status: PATCHED

> NJH-CORRECTION: "[30.15] says 100 sea hexes is Bxx29. But that's wrong. Counting hexes, crossing med, the correct hexrow is Bxx24." The [30.23] bombardment example describes bombardment of Derna, which is 5 hexes beyond Bxx29. Also [24.5] mentions Derna bypass road was built to avoid coastal shelling.

#### [30.5] NAVAL TRANSPORT
> ERRATA: "The reference in the first paragraph should be to 56.0. Note Bene: This Case is a bit screwed up, so read the following corrections carefully."

#### [30.55]
> ERRATA: "While the restriction about expending capability points is true, there is an exception: A unit that has undergone barrage/bombardment may still be transferred."

#### [30.57] PORT CAPACITY AND TROOP TRANSPORT
> ERRATA (IMPORTANT): "The reference to 30.59 should be ignored. The rule is as follows: For every Stacking Point transported in, reduce the Maximum Tonnage for that stage by 10%. (SPs shipped out have no effect.) Thus, if 1 SP were shipped into Tobruk, its maximum tonnage of supplies for that turn would be reduced by 10% to 1530 (1700-170). For ports with incoming capacity of more than one SP, if they bring in at least 50% of their supply tonnage maximum, reduce the SP level by at least 1/3. For ports with 1 SP maximum, shipping in supplies has no effect."

---

## PART III: LOGISTICS GAME

---

### [48.0]–[58.0] LOGISTICS OVERVIEW
Layer: ORIGINAL + ERRATA + NJH
Status: PATCHED

The Logistics Game replaces the abstract supply rules of the Land Game (Section 32.0) with a detailed system tracking fuel, water, ammunition, and stores for every unit.

> ERRATA [32.0]: "Please be warned. The abstract rules — all of them, everywhere in the game — have never been tested. They should work, but they may not." We do NOT use the abstract rules.

### [49.0] FUEL
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [49.1] FUEL CONSUMPTION
> ERRATA (ADDITION): "The Fuel Consumption Rate for Trucks and Recce/AC units is '1'."

#### [49.13]
> ERRATA: "The 'Note' refers only to land units."

#### [49.14] FUEL CAPACITY
> ERRATA (CLARIFICATION): "Although units have a Fuel Capacity, they are not, strictly speaking, limited by it. They may always take fuel from a source (dump, truck, etc.) in the same hex and may always move beyond their 'fuel capacity' limits, if they have expended the fuel points necessary to that movement segment."

#### [49.4] NO-FUEL EFFECTS
> ERRATA (ADDITION): "Infantry-type units in trucks (motorized) that have no fuel may not Close Assault unless they get out of the trucks. Such units may defend at normal strengths, but they are considered to have their non-motorized CPA. Mechanized units and tank/AC/recce units may not Close Assault or Armour Assault without Fuel. They defend at normal strength. However, there will be a two-column adjustment in favor of the attacker if the defending tank-type units have no fuel."

### [50.0] AMMUNITION
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [50.12] NO-AMMO SURRENDER
> ERRATA (IMPORTANT): "For a unit without ammunition to surrender, it must be assaulted (either anti-armor or close). Simply being in an Enemy ZOC or barraged does not cause the unit to surrender."

#### [50.2] AMMO CONSUMPTION
> ERRATA (ADDITION): "Infantry units consume one Ammo point per TOE point used."

### [51.0] STORES
Layer: ORIGINAL + NJH-CHANGE
Status: PATCHED (OPTIONAL CHANGE)

#### [51.1] STORES REQUIREMENTS
> NJH-CHANGE (OPTIONAL): Unit's Stores requirements may be "paid" in "installments" throughout the previous OpStages. Rationale: "Stores can't be used directly off 3rd Line Trucks [51.15] and Stores can't be dumped on ground [54.13]. It's a massive pain and immersion/reality-destroying to attach trucks with enough Stores during Op3 organization (and detach them again later). Also simulation-wise, it's silly that all Stores are consumed instantly, every third OpStage."

### [52.0] WATER
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [52.13] WELL DEPLETION
> ERRATA (IMPORTANT): "The case is wrong, the table is right. You must roll a '1' to deplete a well. Also note that you may draw as much water as you can carry in a major city or oasis."

### [54.0] TRUCKS AND SUPPLY TRANSPORT
Layer: ORIGINAL + NJH-CHANGE
Status: PATCHED (OPTIONAL CHANGES)

#### [54.12] DUMMY DUMPS
> NJH-CHANGE (OPTIONAL): Dummy dumps are NOT revealed by bombing or strafing. "Revealing by air is so trivial as to make subterfuge pointless."

#### [54.13] NO GROUND DUMPING
Layer: ORIGINAL
Status: CANONICAL

Supplies cannot be dumped on the ground — they must be in a truck or an established dump.

#### [54.17] TRUCK CHARACTERISTICS
> ERRATA: "The % under -1 should be '0', under +7, '100'%."

> NJH-CHANGE (OPTIONAL): Ignore [54.2] Truck Characteristics Chart's note about light trucks earning extra breakdown if not moving along road. "Seems wrong, should be heavy trucks. They represent semi-trailers not well suited for off-road. Light trucks are jeeps, 4x4s."

### EVAPORATION AND FUEL IN TANKS
Layer: NJH-CLARIFICATION/CHANGE
Status: PATCHED

**Evaporation rates:** Every Game Turn, 3% of fuel evaporates from containers. British units before a certain date lose 7% because they used 50-gallon drums instead of jerry cans.

> NJH-CLARIFICATION/CHANGE: Fuel in vehicle "fuel tanks" is NOT subject to Evaporation. "A rule that may be implicit but never stated. Either way this is a massive tracking effort reduction win."

> ENGINE NOTE: This is a huge simplification. Without this ruling, the engine would need to track evaporation on every individual vehicle's internal fuel separately from stored fuel. With it, only fuel in drums/dumps/trucks evaporates.

### [55.0] NAVAL CONVOYS AND PORT OPERATIONS
Layer: ORIGINAL + ERRATA + NJH-CORRECTION
Status: PATCHED

#### [55.11]
> ERRATA: "Again, the charts are right, the Case wrong. Follow the chart (55.3) when it comes to what you want to ship in and out, not this case."

#### [55.3] PORT CAPACITY
> ERRATA: "It is feasible that, in Game Turns where Axis Shipping Capacity is 'G', their ports won't be able to handle the total tonnage arriving. If that is the case, any excess over the usual limit may come in at Tripoli."

#### TOBRUK PORT EFFICIENCY
Layer: NJH-CORRECTION
Status: PATCHED

> NJH-CORRECTION: "The rules, charts, and scenarios claim various values for Tobruk port efficiency. It is 5." Rationale: It's a wide open harbor, but not equivalent to other 10's such as Alexandria and Tripoli. The San Giorgio does not block it.

### [56.0] AXIS CONVOY SYSTEM
Layer: ORIGINAL + ERRATA
Status: PATCHED

#### [56.25]
> ERRATA: "The Axis Player may allocate his arriving tonnage to any OpStage within that turn, unless it has already been so designated elsewhere."

#### [56.29] HISTORICAL RE-ROUTING
> ERRATA (ADDITION): Players not wishing to be hamstrung by the mandated arrival rates on the Axis Convoy Level Chart may choose to historically re-route them. Total the number of times each letter may be used, choose which letter per month, no letter above B may be used twice in succession.

#### [56.31] COASTAL SHIPPING SPEED
> ERRATA (ADDITION): "Axis Coastal Shipping moves four 'Tripoli-Tunis' boxes per Stage."

---

## PART IV: AIR GAME
Layer: ORIGINAL + ERRATA + NJH-CHANGE (extensive)
Status: HEAVILY PATCHED

> NOTE: The Air Game is the most problematic subsystem. The designer himself said the air combat system "sucks." The CNA Play Group did extensive revisions. NJHarman did an independent comprehensive rewrite. We adopt NJHarman's Air War rules as our baseline (CC BY-SA 4.0) since they are freely available, well-documented, and designed for the same type of systematic processing our engine requires.

> The full NJHarman Air War rules are extensive and cover: squadron-based missions, OCAP/DCAP rework, air-to-air combat, flak, sighting/recon, strafing, bombardment, friendly fire, and aircraft characteristic changes. These are documented in a separate file: `CNA_AIR_RULES.md`.

### KEY ERRATA FOR AIR GAME (from SPI)

#### [34.72]
> ERRATA: SGSU represents where grounded planes are; they do not literally represent the planes themselves.

#### [35.23]
> ERRATA: "The section in the rulesbook is wrong; the chart book is right. British initial squadron capacity is 12/4, not 15/5."

#### [37.31]
> ERRATA: "Planes in facilities located in major cities may always fly any mission even if there is an Enemy unit adjacent. Facilities in major cities are immune to Enemy combat units moving adjacent."

#### [40.15]
> ERRATA: "The last number is wrong. It should be '12', as Bf109F's have a TacAir of '6'."

#### [40.93]
> ERRATA: "That 20C should be 'ZOC'."

#### [41.5] AIR BOMBARDMENT TABLE
> ERRATA (IMPORTANT): "The 'Barrage Points' row has been screwed up: 7, 8, 9, 10 have been placed over the same column. Each column should have only two numbers, thus place 9, 10 in the next column, move the rest of the numbers one column to the right, and the last column should read 21+ (not 1+). In addition, Flak Suppression should read Flak Destruction."

#### [42.46]
> ERRATA: "Fuel may not be airdropped."

#### [42.47]
> ERRATA (ADDITION): "Units that have been airdropped are considered to have used 5 CPs already."

#### [44.28]
> ERRATA: "The second two squadrons of Ju88A's, from the Malta Table, are planes that are not available during the regular course of the game. Losses to these planes are not considered."

#### [46.4] FLAK TABLE
> ERRATA: "This chart is wrong; the notes at the bottom of Case 46.3 are correct."

---

## PART V: SCENARIOS
Layer: ORIGINAL + ERRATA + NJH
Status: PATCHED

### [59.0] SCENARIO GENERAL RULES

#### [59.2]
> ERRATA: "The letters Cp are the abbreviation for Corps. Also the letters TOE stand for Table of Organization and Equipment."

#### [59.45] INITIAL TRUCK LOADING
> ERRATA (IMPORTANT): "All trucks may be loaded with whatever the player wants. Moreover, at the start of a scenario, a Player may load his trucks with supplies above and beyond what is listed as being available."

### [60.0] THE ITALIAN OFFENSIVE (CAMPAIGN START)

#### SCENARIO START NOTES
Layer: NJH-CLARIFICATION
Status: PATCHED

- Italian Campaign Scenario starts with OpStage I. There is no Strategic Air, Naval Convoy, or Stores Expenditure stages on GT1.
- Prior to OpStage I, Axis player must plan Naval Convoys for GT1 and GT2. First supplies unload in OpStage I.
- All Trucks may be filled with "extra" supply. Coastal shipping starts empty.
- Weather [29.0] IS rolled for on 1st Turn of scenario (unlike many games that dictate GT1 weather).

#### [60.31]
> ERRATA: "Under 'Anywhere in Libya', the XXI Corps Artillery is listed twice."

#### [60.32]
> ERRATA: "The Italian plane listed as 2501 should be Z501."

#### [60.44] UNLIMITED SUPPLIES
Layer: ORIGINAL + NJH-CLARIFICATION
Status: PATCHED

> NJH-CLARIFICATION: "[60.44] says 'Unlimited Supplies in Cairo/Alexandria.' But [57.0] says only Cairo." Ruling: Only Cairo. "Delta Railroad exists for a reason!"

#### [60.45]
> ERRATA (IMPORTANT): "Only the first paragraph of this Case is correct. Ignore all the information starting with 'The following units are available...'"

#### REPAIR FACILITIES
Layer: NJH-INTERPRETATION
Status: PATCHED

> NJH-INTERPRETATION: "[60.44] says 'Major Repair Facility in (only) Alexandria'. But [22.31] says all hexes of Alexandria and Cairo are Major." Ruling: All hexes of Cairo and both hexes of Alexandria are Major Repair Facilities.

> NJH-INTERPRETATION: "[22.31] says Tobruk is Major Repair Facility. But [60.33] says it's temporary." Ruling: It's temporary. Only major facilities in game are Tripoli, Alexandria, and Cairo.

### [61.0]–[63.0] OTHER SCENARIOS

#### [61.38]
> ERRATA: "The unit listed as ARTR should be 4RTR."

#### [61.41]
> ERRATA (ADDITION): "The German Mobile Tank Recovery Squadron starts in Tripolitania."

#### [62.31, 63.31]
> ERRATA (OMISSION): "Deploy the three CW Tank Delivery Squadrons in Cairo."

#### [62.33]
> ERRATA: "Those 142 Blenheim IV F's should be Blenheim IV's (not F's)."

#### [62.41, 63.41]
> ERRATA (OMISSION): "Deploy the German Mobile Tank Recovery Squadron in Beghazi."

#### [63.3]
> ERRATA: "The Allies should get two airfields, and Degheila gets only 1 SGSU."

---

## PART VI: OOB CORRECTIONS

### MISSING ITALIAN DIVISIONS
Layer: ERRATA
Status: CANONICAL

> ERRATA [4.44b] (IMPORTANT OMISSION): "The OA sheets for three entire Italian Divisions — Sirte, Cirene and Marmarica — have been left out." These are only needed for scenarios before February 1941 (they were gone by then).

### MISSING COMMONWEALTH UNITS
> ERRATA [4.44b]: "The 1st Buffs and 1st Hampshires (CW) start the campaign and Italian scenarios as part of (assigned to) The Matruh Garrison."

### AIRCRAFT CORRECTIONS
> ERRATA [4.44a]: "The Fuel Consumption rating of the Gladiator is '1'."
> ERRATA [4.44b]: "On the CW Air Characteristics Chart, the Legend shows that 'F' = Reconnaissance. That should obviously be 'R' = Reconnaissance."
> ERRATA [4.44b]: "The Italian Air Characteristics chart lists an Re.2000. If I'm not mistaken, that plane appears in no scenario or reinforcement track."
> ERRATA [4.44c]: "The Bf.109E should not have 'D' capability. In addition, the Bf.110 as a fighter has a Maneuver rating of 32 only when on Night missions. Otherwise its rating is 30."

### EQUIPMENT CORRECTIONS
> ERRATA [4.47]: "The Armor Protection Rating for the A9 Cruiser is omitted on the Axis version of the chart. It is correct on the Commonwealth version ('1')."
> ERRATA [4.49]: "The CPA for the 7.62cm Pak(R) is '15'; for the Marder, '25'."
> ERRATA [4.49] (IMPORTANT): "The correct values for the German PzIIIE are 25 1 - 4 - 3 4/4 3 0 (reading across)."

### MISSING ARRIVALS
> ERRATA [4.43a]: GT 28/2: 42d RTR (1 Army); T: 4M. GT 64/2: WD: 32nd Army Tank Bde (2); Tpt 20/15. "In addition, in 76/2 there is a reference to 2/68; ignore it. Refer instead to the newly added 64/2 WD."
> ERRATA [4.44b]: "The arrival date for the French Motor Marine Company is wrong: it should be 'D'."

### GERMAN ARTILLERY
> ERRATA [4.44b]: "Under German Non-Divisional Artillery, the 362 Artillery Battery should have an ID Code of 'x' (not 'w', which is anti-tank equipment)."

---

## PART VII: MAP CORRECTIONS
Layer: ERRATA + NJH
Status: PATCHED

> ERRATA: Map "D": Hexside 2228/2328 should have an escarpment.

> NJH-INTERPRETATION: "On map printed Flak such as in Tripoli is considered heavy flak. Affects Fighter Flak Suppression missions (which can't be flown against heavy flak [40.74])."

> NJH-INTERPRETATION: "Various map inconsistencies (e.g. tiny bits of road extending into adjacent hex) will be ignored. Borrowed from OCS: nubs don't count."

---

## APPENDIX A: NJH OPTIONAL MODULES (CHANGES & ADDITIONS)

These are NJHarman's opinionated changes and additions. Each is independently toggleable in the engine.

### A.1 — MUSSOLINI REQUIREMENTS
Layer: NJH-ADDITION
Status: OPTIONAL

Inspired by OCS DAKII. Limits the Italian player's ability to optimize opening moves beyond historical reasonableness. Forces specific units into Egypt by GT1/III and beyond the Mussolini Line by GT5/I. Requirements end if Allies recapture Sidi Barrani or cross into Libya.

> ENGINE NOTE: This is valuable for historical plausibility. Enable by default for training games.

### A.2 — UNTRAINED UNIT FRICTION
Layer: NJH-ADDITION
Status: OPTIONAL

Units not fully trained:
- May detach/attach Units, Trucks, and Replacement Points only during Organization Phases
- Expend double CPA for detach/attach
- Expend double CPA to absorb TOE replacement points
- Expend CPA for Water Draw, Load/Unload even during Organization Phases

### A.3 — ITALIAN COMMAND & CONTROL FRICTION
Layer: NJH-ADDITION
Status: OPTIONAL

Italian units not stacked with Rommel:
- Expend double CPA for Organization changes
- May detach/attach only during Organization Phases
- May NOT detach/attach at all during 1st OpStage of campaign

### A.4 — ITALIAN TRUCK SHUFFLE
Layer: NJH-ADDITION
Status: OPTIONAL

At most two Italian semi-motorized Divisions may be motorized concurrently. No such Div may remain motorized for more than 8 consecutive OpStages.

### A.5 — OPERATION HERKULES (MALTA INVASION)
Layer: NJH-ADDITION
Status: OPTIONAL

Based on OCS DAKII. Requires: July 1941+, ≤3 active Malta squadrons, Hitler sign-off (1-2 on d6), invasion prep, withdrawal of all Ju52s. Result: Malta captured, Allied aircraft/bases lost, Axis gains airbase.

### A.6 — OPERATION ALBUMEN
Layer: NJH-ADDITION
Status: OPTIONAL

Once per game, after fall of Crete, Allied SBS/SAS may raid Crete air facilities. Uses [27.93] SAS Raid Table.

### A.7 — RECON BY RECCE
Layer: NJH-ADDITION
Status: OPTIONAL

Adds a "Recon by Recce" step at start of friendly Movement Segment. Only patrol-eligible units ([16.11]) may Recon. Expends 2 non-movement CP plus movement cost. Reveals enemy unit information without actually moving units on map.

### A.8 — PINNING OVERHAUL
Layer: NJH-CHANGE
Status: OPTIONAL

Unifies Land Barrage Pinned and Air Bombardment Pinned. Pinned effects last until end of OpStage. Comprehensive list of prohibited/restricted actions for pinned units. Addresses the "cliff" between Close Assault vs all-pinned units and vs even one unpinned company.

### A.9 — THE SAN GIORGIO
Layer: NJH-CHANGE + NJH-ADDITION
Status: OPTIONAL

Fixes San Giorgio rules and adds CW Fleet ability to attack it. Rescinds [55.25]. See NJHarman house rules for full text.

### A.10 — COMMONWEALTH FLEET ENHANCEMENTS
Layer: NJH-CHANGE + NJH-ADDITION
Status: OPTIONAL

Ship flak while in port, BB/CA inland bombardment at half strength, fleet movement during OpStage, coastal road interdiction.

### A.11 — COHESION RECOVERY LIMITATION
Layer: NJH-CHANGE
Status: OPTIONAL

Air Bombardment pin prevents cohesion recovery per [6.24-1].

### A.12 — ZOC AND BREAKING OFF CHANGES
Layer: NJH-CHANGE
Status: OPTIONAL

Three changes: (1) Reacting-away units don't impede, (2) ZOC-negating units must stay, (3) Engaged units can't move EZOC-to-EZOC even with negation. Also: Engaged persists until 4 CPA spent to remove; Contact removed at Movement Segment start or 2 CPA.

---

## APPENDIX B: THE DESIGNER'S BLESSING

> "To believe that a game of the breadth and extent of CNA would not have any mistakes was to have the faith of the fanatically insane. We would like to make a pitch here for initiative: if you find something that is obviously wrong, instead of putting the game aside and waiting three months for the answer to your question, please try to resolve it yourself. After all, these games were not delivered on mountain tops writ on fiery tablets...the designer simply made up everything you have read (except the hardware.) And if he can do it, so can you. Try it."
>
> — Richard Berg, CNA Errata, September 1979

---

## DOCUMENT STATUS

| Section | Coverage | Sources Applied |
|---------|----------|----------------|
| Sequence of Play [5.0] | Complete | L1 + L2 + L5 (NJH restructured) |
| CPA System [6.0] | Complete | L1 + L2 + L5 |
| Initiative [7.0] | Complete | L1 + L5 |
| Movement [8.0] | Substantial | L1 + L2 + L3 + L4 |
| Stacking [9.0] | Errata only | L1 + L2 |
| ZOC [10.0] | Complete | L1 + L2 + L3 + L5 |
| Combat System [11.0–15.0] | Substantial | L1 + L2 + L3 + L4 |
| Patrols [16.0] | Errata + reminder | L1 + L2 |
| Morale [17.0] | Errata only | L1 + L2 |
| Organization [19.0] | Errata only | L1 + L2 |
| Reinforcements [20.0] | Errata only | L1 + L2 |
| Breakdown/Repair [21.0–22.0] | Partial | L1 + L2 + L4 |
| Engineers/Construction [23.0–24.0] | Errata only | L1 + L2 |
| Fortifications/Mines [25.0–26.0] | Not yet | L1 |
| Raiders [27.0] | Errata only | L1 + L2 |
| Weather [29.0] | Complete | L1 + L2 |
| Fleet [30.0] | Substantial | L1 + L2 + L3 + L5 |
| Abstract Rules [32.0] | SKIPPED | N/A |
| Air Game [33.0–47.0] | Errata + NJH rewrite ref | L1 + L2 + L5 |
| Logistics [48.0–58.0] | Substantial | L1 + L2 + L3 + L5 |
| Scenarios [59.0–65.0] | Errata + clarifications | L1 + L2 + L3 + L4 |
| OOB Corrections | Complete (from errata) | L1 + L2 |
| Map Corrections | Complete | L1 + L2 + L4 |

### NEXT STEPS
- [ ] Full Air Game rules document (NJH rewrite) — separate file
- [ ] Terrain Effects Chart with all errata applied
- [ ] Combat Results Tables with all errata applied
- [ ] Unit characteristics tables with all corrections
- [ ] Scenario setup details with all errata/omissions fixed
- [ ] Integration of War With A Mate discoveries as they publish
- [ ] Cross-validation with CNA Play Group interpretations (if access obtained)
