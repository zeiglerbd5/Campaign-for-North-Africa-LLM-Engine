// Copyright (c) 2024 Michael D Henderson. All rights reserved.

package wxx

// Direction_e is an enum for the direction
type Direction_e int

const (
	Unknown Direction_e = iota
	NorthEast
	East
	SouthEast
	West
	SouthWest
	NorthWest
)
const (
	NumDirections = int(NorthWest) + 1
)

// Directions is a helper for iterating over the directions
var Directions = []Direction_e{
	NorthEast,
	East,
	SouthEast,
	SouthWest,
	West,
	NorthWest,
}
