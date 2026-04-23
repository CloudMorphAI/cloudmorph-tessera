"""Per-cloud artifact writers.

ArtifactWriter is the interface; each cloud has an implementation that
extracts the per-executor `_upload_artifacts` logic from main.py.

The writer is responsible for the cloud-specific PUT — bucket / region /
auth resolution lives upstream in Settings.
"""

from cloudmorph_common.artifacts.base import ArtifactWriter, NoOpArtifactWriter
from cloudmorph_common.artifacts.s3 import S3ArtifactWriter

__all__ = ["ArtifactWriter", "NoOpArtifactWriter", "S3ArtifactWriter"]

# Optional imports — only available when the relevant extra is installed.
try:
    from cloudmorph_common.artifacts.gcs import GcsArtifactWriter  # noqa: F401

    __all__.append("GcsArtifactWriter")
except ImportError:
    pass

try:
    from cloudmorph_common.artifacts.blob import BlobArtifactWriter  # noqa: F401

    __all__.append("BlobArtifactWriter")
except ImportError:
    pass
