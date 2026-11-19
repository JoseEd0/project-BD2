"""Errores del servicio: los del corpus dicen qué paso falló y por qué, para registrarlo y
seguir; los de la ingesta y el modelo detienen el trabajo."""


class CorpusError(Exception):
    """El corpus no se puede construir como se pidió."""


class HarvestError(CorpusError):
    """El servidor OAI-PMH de arXiv respondió algo que no es una lista de registros."""


class UnreadablePdfError(CorpusError):
    """El PDF está dañado: pypdf no puede leer su estructura."""


class TextlessPdfError(CorpusError):
    """El PDF se lee pero casi no tiene texto: lo más probable es que sea escaneado."""


class ModelNotFoundError(Exception):
    """El modelo de embeddings no está en la carpeta configurada."""


class EmbeddingCacheError(Exception):
    """La caché de embeddings no corresponde al modelo con que se abre."""


class IngestError(Exception):
    """La ingesta en el gestor no terminó como se esperaba: el gestor rechazó filas o la
    tabla quedó con otra cantidad de filas que el corpus."""
