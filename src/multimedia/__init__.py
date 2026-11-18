"""Multimedia: de imágenes y audio a vectores que un índice puede comparar.

`extract_features` es el `ExtractFeatures(objeto)` del enunciado: de una imagen (SIFT) o
de un audio (MFCC) a la matriz de sus descriptores locales. El vocabulario los cuantiza en
palabras visuales o auditivas, el TF-IDF las pesa y el almacén de características guarda
el histograma de cada fila. No conoce el SQL ni el API.
"""

from .audio import (
    MFCC_DELTA_ORDERS,
    DecodedAudio,
    audio_duration,
    decode_audio,
    describe_fragment,
    mfcc_dimension,
)
from .descriptors import Descriptors
from .errors import (
    EmptyFragmentError,
    FragmentOnImageError,
    FragmentOutOfRangeError,
    MalformedFragmentError,
    MediaError,
    MediaFragmentError,
    UnreadableMediaError,
    UnsupportedMediaTypeError,
)
from .extraction import check_media, descriptor_dimension, extract_features
from .feature_store import FeatureStore, FeatureStoreBuild, FeatureStoreError
from .fragments import MediaReference, TemporalFragment, parse_fragment, split_fragment
from .image import SIFT_DESCRIPTOR_DIMENSION
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, MediaKind, media_kind_of, media_reference
from .parallel import (
    IN_FLIGHT_CHUNKS_PER_WORKER,
    ExtractedFeatures,
    ExtractionOutcome,
    FailedExtraction,
    extract_each,
    extract_in_parallel,
)
from .segments import (
    SegmentedAudio,
    SegmentLimitError,
    decode_segments,
    describe_segments,
    segment_windows,
)
from .vocabulary import (
    NotEnoughDescriptorsError,
    Vocabulary,
    training_positions,
    training_sample,
)
from .weighting import inverse_document_frequencies, tf_idf

__all__ = [
    "AUDIO_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "IN_FLIGHT_CHUNKS_PER_WORKER",
    "MFCC_DELTA_ORDERS",
    "SIFT_DESCRIPTOR_DIMENSION",
    "DecodedAudio",
    "Descriptors",
    "EmptyFragmentError",
    "ExtractedFeatures",
    "ExtractionOutcome",
    "FailedExtraction",
    "FeatureStore",
    "FeatureStoreBuild",
    "FeatureStoreError",
    "FragmentOnImageError",
    "FragmentOutOfRangeError",
    "MalformedFragmentError",
    "MediaError",
    "MediaFragmentError",
    "MediaKind",
    "MediaReference",
    "NotEnoughDescriptorsError",
    "SegmentLimitError",
    "SegmentedAudio",
    "TemporalFragment",
    "UnreadableMediaError",
    "UnsupportedMediaTypeError",
    "Vocabulary",
    "audio_duration",
    "check_media",
    "decode_audio",
    "decode_segments",
    "describe_fragment",
    "describe_segments",
    "descriptor_dimension",
    "extract_each",
    "extract_features",
    "extract_in_parallel",
    "inverse_document_frequencies",
    "media_kind_of",
    "media_reference",
    "mfcc_dimension",
    "parse_fragment",
    "segment_windows",
    "split_fragment",
    "tf_idf",
    "training_positions",
    "training_sample",
]
