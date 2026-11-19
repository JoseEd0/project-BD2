"""Detección de copyright en audio (Parte 5, opción C).

El catálogo de canciones originales es `fma_small` del Free Music Archive, cortado en
ventanas que el gestor indexa como tramos de audio (`pista.mp3#t=inicio,fin`) con un HNSW
sobre sus MFCC. El servicio no importa el gestor: le habla por su API REST con `appkit`.
"""
