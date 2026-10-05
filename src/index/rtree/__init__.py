"""Índice R-Tree sobre disco para datos espaciales."""

from .secondary import NotSpatialColumnError, SpatialIndex
from .tree import Neighbor, RTree, SearchStats

__all__ = ["Neighbor", "NotSpatialColumnError", "RTree", "SearchStats", "SpatialIndex"]
