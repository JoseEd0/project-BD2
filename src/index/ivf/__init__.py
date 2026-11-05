"""Índice IVF de vectores sobre FAISS."""

from .index import IVFIndex, default_list_count, default_probe_count

__all__ = ["IVFIndex", "default_list_count", "default_probe_count"]
