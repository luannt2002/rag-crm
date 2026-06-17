"""Domain events.

Ref: PLAN_04 §events/.
"""

from ragbot.domain.events.base import DomainEvent
from ragbot.domain.events.chat_events import (
    ChatAnswered,
    ChatDeliveryFailed,
    ChatFailed,
    ChatReceived,
    FeedbackGiven,
)
from ragbot.domain.events.document_events import (
    CorpusVersionBumped,
    DocumentArchived,
    DocumentFailed,
    DocumentIngested,
    DocumentPurged,
    DocumentUploaded,
)

__all__ = [
    "ChatAnswered",
    "ChatDeliveryFailed",
    "ChatFailed",
    "ChatReceived",
    "CorpusVersionBumped",
    "DocumentArchived",
    "DocumentEvent",
    "DocumentFailed",
    "DocumentIngested",
    "DocumentPurged",
    "DocumentUploaded",
    "DomainEvent",
    "FeedbackGiven",
]
