// Copyright (c) 2024 Michael D Henderson. All rights reserved.

package wxx

// Key constants from wxx.go defining the CNA board dimensions:
//
// MAX_COLUMNS = 166  (columns across the entire board, sections A-E)
// MAX_ROWS    = 63   (rows on the board)
//
// Each section (A-E) is 33 columns wide, except E which has the remainder.
// Board A: columns 1-33
// Board B: columns 34-66
// Board C: columns 67-99
// Board D: columns 100-132
// Board E: columns 133-166
//
// Hex grid is ROW-oriented (flat-top hexes laid out in rows).
// hexWidth=40.0, hexHeight=46.18 (for Worldographer export)
//
// Terrain mapping from CSV terrain strings to enum:
//   "Clear"      -> Clear
//   "Delta"      -> Delta
//   "Desert"     -> Desert
//   "Gravel"     -> RockGravel
//   "Mountain"   -> Mountains
//   "Rough"      -> Rough
//   "Salt Marsh" -> SaltMarsh
//   "Sea"        -> Sea
//   "Swamp"      -> Swamp
//   "Vegetation" -> HeavyVegetation
//
// The wxx.go Create() function transforms hex coordinates:
//   column = hex.Column - 1  (convert from 1-based to 0-based)
//   row = MAX_ROWS - hex.Row (flip origin from top-left to bottom-left)
//
// See the full wxx.go source for the complete XML generation code.
// The file generates Worldographer-compatible .wxx map files.
