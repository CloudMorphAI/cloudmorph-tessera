"""Hypothesis property tests for HashChain and canonical_json."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tessera.audit.canonical_json import canonical_json
from tessera.audit.chain import HashChain

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A valid event dict: must have tenantId; values are simple JSON-safe scalars.
_simple_value = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.text(max_size=50),
)

_event_strategy = st.fixed_dictionaries(
    {"tenantId": st.text(min_size=1, max_size=40, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"))},
    optional={"payload": st.dictionaries(st.text(min_size=1, max_size=10), _simple_value, max_size=5)},
)

# A list of at least 1 event (all sharing the same tenantId handled via map below)
_event_list_strategy = st.lists(_event_strategy, min_size=1, max_size=20)


# ---------------------------------------------------------------------------
# Property 1: verify always succeeds on a clean stamped chain
# ---------------------------------------------------------------------------


@given(events=_event_list_strategy)
@settings(max_examples=100)
def test_stamped_chain_always_verifies(events: list[dict]) -> None:
    """Stamping any sequence of valid events produces a self-consistent chain."""
    chain = HashChain()
    stamped_events = [chain.stamp(e) for e in events]

    for ev in stamped_events:
        assert HashChain.verify_event_hash(ev), "verify_event_hash failed on stamped event"

    for prev, nxt in zip(stamped_events, stamped_events[1:]):
        if prev["tenantId"] == nxt["tenantId"]:
            # Only consecutive same-scope events must link
            assert HashChain.verify_pair(prev, nxt), "verify_pair failed on consecutive same-scope events"


# ---------------------------------------------------------------------------
# Property 2: Mutating any byte of eventHash breaks verify_event_hash
# ---------------------------------------------------------------------------


@given(events=_event_list_strategy)
@settings(max_examples=100)
def test_mutate_event_hash_breaks_verify(events: list[dict]) -> None:
    chain = HashChain()
    stamped = chain.stamp(events[0])
    original_hash = stamped["eventHash"]

    # Flip the last hex char
    flipped_char = "0" if original_hash[-1] != "0" else "1"
    mutated = {**stamped, "eventHash": original_hash[:-1] + flipped_char}
    assert not HashChain.verify_event_hash(mutated), "mutated eventHash should not verify"


# ---------------------------------------------------------------------------
# Property 3: Adjacent swap breaks verify_pair when chain has >= 2 events
# ---------------------------------------------------------------------------


@given(events=st.lists(_event_strategy, min_size=2, max_size=20))
@settings(max_examples=100)
def test_adjacent_swap_breaks_verify_pair(events: list[dict]) -> None:
    # Force all events to the same tenantId so consecutive events always link
    tenant = events[0]["tenantId"]
    normalised = [{**e, "tenantId": tenant} for e in events]

    chain = HashChain()
    stamped = [chain.stamp(e) for e in normalised]

    # Swap first two events — now second.prevEventHash points to something before first
    swapped_0, swapped_1 = stamped[1], stamped[0]
    # The "new" pair (swapped_0 then swapped_1) should fail verify_pair
    # because swapped_1.prevEventHash was set to "" (first event) but
    # swapped_0.prevEventHash was set to stamped[0].eventHash — they don't link.
    assert not HashChain.verify_pair(swapped_0, swapped_1), (
        "swapped adjacent events should not satisfy verify_pair"
    )


# ---------------------------------------------------------------------------
# Property 4: canonical_json is deterministic across key orderings
# ---------------------------------------------------------------------------


@given(
    keys=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=10, unique=True),
    values=st.lists(_simple_value, min_size=1, max_size=10),
)
@settings(max_examples=200)
def test_canonical_json_deterministic(keys: list[str], values: list[object]) -> None:
    """Same key-value pairs in any order produce identical canonical bytes."""
    # Pad or truncate values to match keys length
    pairs = list(zip(keys, (values * len(keys))[: len(keys)]))

    d_forward = dict(pairs)
    d_reverse = dict(reversed(pairs))

    # Filter out any booleans used as values when they coincide with None — all fine for bytes
    result_forward = canonical_json(d_forward)
    result_reverse = canonical_json(d_reverse)

    assert result_forward == result_reverse, (
        f"canonical_json not deterministic: {result_forward!r} != {result_reverse!r}"
    )
