"""CloudMorph Control Centre SDK.

Usage::

    from cloudmorph import CloudMorph

    cm = CloudMorph(token="cm_...")
    result = cm.request("aws.s3.list_buckets", account_id="acc_123")
    print(result)
"""

from cloudmorph.client import CloudMorph, CloudMorphError, RateLimitError

# CloudMorphClient is the canonical public name; CloudMorph kept for backwards compat.
CloudMorphClient = CloudMorph

__all__ = ["CloudMorphClient", "CloudMorph", "CloudMorphError", "RateLimitError"]
__version__ = "0.1.0"
