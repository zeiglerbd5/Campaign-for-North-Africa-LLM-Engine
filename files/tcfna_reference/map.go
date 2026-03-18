/*******************************************************************************
 * TCFNA - Game Engine for SPI's Campaign for North Africa
 * Copyright (C) 2022. Michael D Henderson
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as published
 * by the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 ******************************************************************************/

package model

import "fmt"

type MAP struct {
	Hexes  map[string]*HEX `json:"hexes"`
	Sorted HEXES
}

type TERRAIN struct {
	Section string `json:"section,omitempty"`
}

type Terrain struct {
	Airfield                TERRAIN `json:"airfield,omitempty"`
	Border                  TERRAIN `json:"border,omitempty"`
	Clear                   TERRAIN `json:"clear,omitempty"`
	Coast                   TERRAIN `json:"coast,omitempty"`
	Delta                   TERRAIN `json:"delta,omitempty"`
	Desert                  TERRAIN `json:"desert,omitempty"`
	Escarpment              TERRAIN `json:"escarpment,omitempty"`
	FlyingBoatAlightingArea TERRAIN `json:"flying boat alighting area,omitempty"`
	FlyingBoatBasin         TERRAIN `json:"flying boat basin,omitempty"`
	HeavyVegetation         TERRAIN `json:"heavy vegetation,omitempty"`
	MajorCity               TERRAIN `json:"major city,omitempty"`
	MajorRiver              TERRAIN `json:"major river,omitempty"`
	MinorRiver              TERRAIN `json:"minor river,omitempty"`
	Mountain                TERRAIN `json:"mountain,omitempty"`
	OffMapAirfield          TERRAIN `json:"off map airfield,omitempty"`
	OffMapFlyingBoatBasin   TERRAIN `json:"off map flying boat basin,omitempty"`
	Oasis                   TERRAIN `json:"oasis,omitempty"`
	Port                    TERRAIN `json:"port,omitempty"`
	Railroad                TERRAIN `json:"railroad,omitempty"`
	Ridge                   TERRAIN `json:"ridge,omitempty"`
	Road                    TERRAIN `json:"road,omitempty"`
	RockGravel              TERRAIN `json:"rock/gravel,omitempty"`
	Rough                   TERRAIN `json:"rough,omitempty"`
	SaltMarsh               TERRAIN `json:"salt marsh,omitempty"`
	Sea                     TERRAIN `json:"sea,omitempty"`
	Slope                   TERRAIN `json:"slope,omitempty"`
	Swamp                   TERRAIN `json:"swamp,omitempty"`
	Track                   TERRAIN `json:"track,omitempty"`
	TrainingArea            TERRAIN `json:"training area,omitempty"`
	UnfinishedRailroad      TERRAIN `json:"unfinished railroad,omitempty"`
	UnfinishedRoad          TERRAIN `json:"unfinished road,omitempty"`
	VillageBir              TERRAIN `json:"village/bir,omitempty"`
	Wadi                    TERRAIN `json:"wadi,omitempty"`
}

// Board_t is the entire board, which is composed of 5 different sections (sheets).
type Board_t struct {
	// hexes are indexed by row then column?
	Hexes [33][166]Hex_t
}

// Hex_t is a single hexagon on the board.
type Hex_t struct {
	ID string // unique identifier for the hex on the board

	Row    int // which row (1-47) of the board the hex is on
	Column int // which column (1-166) of the board the hex is on

	Sheet       string // which section (A-E) the hex is on
	SheetRow    int    // which row (1-47) of the section the hex is on
	SheetColumn int    // which column (1-34) of the section the hex is on

	Label   string // label to display on the hex, e.g. "C3102"
	Terrain Terrain_t
	Coast   bool // some hexes are coastal, meaning sea plus another terrain

	Name       string // name of the habitation, if any
	Habitation Habitation_t
	Port       bool // some habitations on the coast are ports

	mapHex        string
	hexColumn     string
	hexRow        string
	hexRRCCC      string
	name          string
	terrain       string
	habitation    string
	misc          string
	hsElevationNE string
	hsElevationE  string
	hsElevationSE string
	hsElevationSW string
	hsElevationW  string
	hsElevationNW string
	mapHex2       string
	hsTransNE     string
	hsTransE      string
	hsTransSE     string
	hsTransSW     string
	hsTransW      string
	hsTransNW     string
	hsWaterNE     string
	hsWaterE      string
	hsWaterSE     string
	hsWaterSW     string
	hsWaterW      string
	hsWaterNW     string
	mapHex3       string
}

type Terrain_t int

const (
	TClear Terrain_t = iota
	TDelta
	TDesert
	THeavyVegetation
	TMountain
	TRockGravel
	TRough
	TSaltMarsh
	TSea
	TSwamp
)

func (t Terrain_t) String() string {
	switch t {
	case TClear:
		return "Clear"
	case TDelta:
		return "Delta"
	case TDesert:
		return "Desert"
	case THeavyVegetation:
		return "Heavy Vegetation"
	case TMountain:
		return "Mountain"
	case TRockGravel:
		return "Rock/Gravel"
	case TRough:
		return "Rough"
	case TSaltMarsh:
		return "Salt Marsh"
	case TSea:
		return "Sea"
	case TSwamp:
		return "Swamp"
	default:
		return fmt.Sprintf("Terrain(%d)", t)
	}
}

type Habitation_t int

const (
	HNone Habitation_t = iota
	HOasis
	HVillageBir
	HMajorCity
)

type Elevation_t int

const (
	ENone Elevation_t = iota
	EEscarpmentDown
	EEscarpmentUp
	ESlopeDown
	ESlopeUp
	ERidge
)
