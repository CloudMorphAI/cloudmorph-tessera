"""Tessera audit subsystem — public API.

Breaking change (v0.2.0): ``BufferedSink`` has been removed from public exports.
Import it directly from ``tessera.audit.sinks._buffered`` if you need it.
"""

from tessera.audit import canonical_json
from tessera.audit.chain import HashChain
from tessera.audit.emitter import AuditEmitter
from tessera.audit.sinks.base import AuditSink
from tessera.audit.sinks.sqlite import SqliteSink
from tessera.audit.sinks.stdout import StdoutSink

__all__ = [
    "HashChain",
    "AuditEmitter",
    "AuditSink",
    "SqliteSink",
    "StdoutSink",
    "canonical_json",
]
