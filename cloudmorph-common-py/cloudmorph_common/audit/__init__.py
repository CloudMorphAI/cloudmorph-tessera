"""Tamper-evident audit chain + pluggable sinks.

Public API:

    from cloudmorph_common.audit import AuditEmitter, BufferedSink, StdoutSink, S3Sink
    from cloudmorph_common.audit.chain import HashChain
    from cloudmorph_common.audit.canonical_json import canonical_json
"""

from cloudmorph_common.audit.canonical_json import canonical_json
from cloudmorph_common.audit.chain import HashChain
from cloudmorph_common.audit.emitter import AuditEmitter
from cloudmorph_common.audit.sinks.buffered import BufferedSink
from cloudmorph_common.audit.sinks.s3 import S3Sink
from cloudmorph_common.audit.sinks.stdout import StdoutSink

__all__ = [
    "AuditEmitter",
    "BufferedSink",
    "HashChain",
    "S3Sink",
    "StdoutSink",
    "canonical_json",
]
