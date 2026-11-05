"""Multimedia: de imágenes y audio a vectores que un índice puede comparar.

`extract_features` es el `ExtractFeatures(objeto)` del enunciado: de una imagen (SIFT) o
de un audio (MFCC) a la matriz de sus descriptores locales. El vocabulario los cuantiza en
palabras visuales o auditivas, el TF-IDF las pesa y el almacén de características guarda
el histograma de cada fila. No conoce el SQL ni el API.
"""

from .audio import MFCC_DELTA_ORDERS, mfcc_dimension
from .descriptors import Descriptors
from .errors import MediaError, UnreadableMediaError, UnsupportedMediaTypeError
from .extraction import descriptor_dimension, extract_features
from .feature_store import FeatureStore, FeatureStoreBuild, FeatureStoreError
from .image import SIFT_DESCRIPTOR_DIMENSION
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, MediaKind, media_kind_of
from .vocabulary import NotEnoughDescriptorsError, Vocabulary, training_sample
from .weighting import inverse_document_frequencies, tf_idf

__all__ = [
    "AUDIO_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "MFCC_DELTA_ORDERS",
    "SIFT_DESCRIPTOR_DIMENSION",
    "Descriptors",
    "FeatureStore",
    "FeatureStoreBuild",
    "FeatureStoreError",
    "MediaError",
    "MediaKind",
    "NotEnoughDescriptorsError",
    "UnreadableMediaError",
    "UnsupportedMediaTypeError",
    "Vocabulary",
    "descriptor_dimension",
    "extract_features",
    "inverse_document_frequencies",
    "media_kind_of",
    "mfcc_dimension",
    "tf_idf",
    "training_sample",
]
