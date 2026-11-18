"""Errores del corpus: cada uno dice qué paso falló y por qué, para registrarlo y seguir."""


class CorpusError(Exception):
    """El corpus no se puede construir como se pidió."""


class HarvestError(CorpusError):
    """El servidor OAI-PMH de arXiv respondió algo que no es una lista de registros."""


class UnreadablePdfError(CorpusError):
    """El PDF está dañado: pypdf no puede leer su estructura."""


class TextlessPdfError(CorpusError):
    """El PDF se lee pero casi no tiene texto: lo más probable es que sea escaneado."""
