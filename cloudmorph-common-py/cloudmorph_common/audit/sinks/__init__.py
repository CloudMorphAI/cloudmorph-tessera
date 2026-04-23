"""Audit log sinks. Pluggable; AuditEmitter routes to one or more.

Built-in sinks:
- StdoutSink: writes one JSON line per event to stdout. Container log driver
  picks up. Always-on default.
- S3Sink: writes to a managed CloudMorph-owned S3 bucket. Per-tenant prefix.
- BufferedSink: wraps another sink; on failure, buffers to disk-backed bounded
  queue with drop-oldest-on-overflow. The reliability backstop.

Post-MVP sinks: KafkaSink, ClickHouseSink, S3CustomerOwnedSink (cross-account
role write), GcsSink, AzureBlobSink.

Implement the Sink protocol below to add new sinks.
"""

from cloudmorph_common.audit.sinks.buffered import BufferedSink
from cloudmorph_common.audit.sinks.s3 import S3Sink
from cloudmorph_common.audit.sinks.stdout import StdoutSink

__all__ = ["BufferedSink", "S3Sink", "StdoutSink"]
