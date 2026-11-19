"""CLIP ViT-B/32 en ONNX: embeddings de fotos y de textos en el mismo espacio, sin PyTorch."""

from .batches import BatchSettings, EmbeddingRun, PixelEncoder, fill_cache
from .cache import EmbeddingCache, image_digest
from .encoder import EMBEDDING_DIMENSION, ClipImageEncoder, ClipTextEncoder, load_image
from .errors import ClipError, EmbeddingCacheError, ModelNotFoundError, UnreadableImageError
from .models import ModelPaths, download_models, locate_models, models_directory_from_environment
from .preprocessing import IMAGE_SIDE, pixel_values
from .tokenizer import CONTEXT_LENGTH, ClipTokenizer

__all__ = [
    "CONTEXT_LENGTH",
    "EMBEDDING_DIMENSION",
    "IMAGE_SIDE",
    "BatchSettings",
    "ClipError",
    "ClipImageEncoder",
    "ClipTextEncoder",
    "ClipTokenizer",
    "EmbeddingCache",
    "EmbeddingCacheError",
    "EmbeddingRun",
    "ModelNotFoundError",
    "ModelPaths",
    "PixelEncoder",
    "UnreadableImageError",
    "download_models",
    "fill_cache",
    "image_digest",
    "load_image",
    "locate_models",
    "models_directory_from_environment",
    "pixel_values",
]
