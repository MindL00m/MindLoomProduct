"""Speaker name normalisation via fuzzy clustering.

Different connectors refer to the same person in inconsistent ways ("Amey",
"Amey A", "Amey Apte"). This module clusters participants whose *names* are
near-duplicates with :mod:`thefuzz`, picks a canonical participant per cluster,
rewrites message ``sender`` references to the canonical participant id, and
collapses the participant list. Every merge is logged for human review.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from thefuzz import fuzz

from config import get_settings
from models import IncomingMessage, Participant

logger = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,}$")


def _is_phone_number(name: str) -> bool:
    """Return ``True`` if ``name`` looks like a raw phone number rather than a name."""

    return bool(_PHONE_RE.match(name.strip()))


def _similarity(a: str, b: str) -> int:
    """Order- and subset-insensitive fuzzy similarity score (0-100).

    ``token_set_ratio`` lets short additions like "Amey" vs "Amey A" score
    highly while avoiding the aggressive substring false-positives that plain
    ``partial_ratio`` produces on short names.
    """

    return max(
        fuzz.token_sort_ratio(a, b),
        fuzz.token_set_ratio(a, b),
    )


def _choose_canonical(cluster: list[Participant], counts: Counter[str]) -> Participant:
    """Pick the canonical participant for a cluster.

    Prefers a non-phone, human-readable name belonging to the most active
    participant; ties are broken by longer (more specific) names.
    """

    def sort_key(participant: Participant) -> tuple[int, int, int]:
        return (
            0 if _is_phone_number(participant.name) else 1,
            counts[participant.id],
            len(participant.name),
        )

    return max(cluster, key=sort_key)


def normalise_speakers(
    messages: list[IncomingMessage],
    participants: list[Participant],
) -> tuple[list[IncomingMessage], list[Participant], dict[str, str]]:
    """Deduplicate likely-identical participants and rewrite message senders.

    Args:
        messages: Conversation messages whose ``sender`` ids reference participants.
        participants: Participants to reconcile by fuzzy name matching.

    Returns:
        A tuple of ``(messages, participants, name_mapping)`` where ``messages``
        have senders remapped to canonical participant ids, ``participants`` is
        the collapsed canonical list, and ``name_mapping`` maps every original
        participant name to its canonical name (for logging/review).
    """

    if not participants:
        return list(messages), list(participants), {}

    settings = get_settings()
    threshold = settings.speaker_similarity_threshold

    # Activity per participant id anchors clusters on the most active member.
    counts: Counter[str] = Counter(message.sender for message in messages)
    ordered = sorted(participants, key=lambda p: (-counts[p.id], p.name))

    clusters: list[list[Participant]] = []
    for participant in ordered:
        placed = False
        for cluster in clusters:
            if any(_similarity(participant.name, member.name) >= threshold for member in cluster):
                cluster.append(participant)
                placed = True
                break
        if not placed:
            clusters.append([participant])

    id_to_canonical_id: dict[str, str] = {}
    name_mapping: dict[str, str] = {}
    canonical_participants: list[Participant] = []

    for cluster in clusters:
        canonical = _choose_canonical(cluster, counts)
        canonical_participants.append(canonical)
        for member in cluster:
            id_to_canonical_id[member.id] = canonical.id
            name_mapping[member.name] = canonical.name
            if member.id != canonical.id:
                logger.info(
                    "Merging participant '%s' (%d msgs) -> '%s' (threshold=%d)",
                    member.name,
                    counts[member.id],
                    canonical.name,
                    threshold,
                )

    remapped_messages = [
        message.model_copy(update={"sender": id_to_canonical_id.get(message.sender, message.sender)})
        for message in messages
    ]

    logger.info(
        "Speaker normalisation: %d participants -> %d canonical participants",
        len(participants),
        len(canonical_participants),
    )

    return remapped_messages, canonical_participants, name_mapping
