// Copyright (c) 2024 Michael D Henderson. All rights reserved.

package wxx

import "fmt"

type Terrain_t int

const (
	Blank Terrain_t = iota // Blank                   0
	Clear
	Delta
	Desert
	HeavyVegetation
	Mountains
	RockGravel
	Rough
	SaltMarsh
	Sea
	Swamp
)

func (t Terrain_t) TileName() string {
	switch t {
	case Blank:
		return "Blank"
	case Clear:
		return "Flat Desert Coastal"
	case Delta:
		return "Flat Farmland"
	case Desert:
		return "Flat Desert Sandy"
	case HeavyVegetation:
		return "Flat Moss"
	case Mountains:
		return "Mountains"
	case RockGravel:
		return "Flat Desert Rocky"
	case Rough:
		return "Flat Mud"
	case SaltMarsh:
		return "Flat Marsh"
	case Sea:
		return "Water Sea"
	case Swamp:
		return "Flat Swamp"
	}
	panic(fmt.Sprintf("assert(terrain != %d)", t))
}
