from typing import Final
from .enums import IncidentStatus, VerificationState

PUBLIC_EVIDENCE_STATES: Final[frozenset[VerificationState]] = frozenset(
    {VerificationState.CORROBORATED, VerificationState.VERIFIED}
)

# A closed incident may retain its public location but never a live viewer asset or frame.
PUBLIC_LOCATION_STATUSES: Final[frozenset[IncidentStatus]] = frozenset(
    {
        IncidentStatus.CANDIDATE,
        IncidentStatus.UNDER_REVIEW,
        IncidentStatus.ACTIVE_CONFIRMED,
        IncidentStatus.MONITORING,
        IncidentStatus.EXTINGUISHED,
        IncidentStatus.CLOSED,
    }
)
VIEWER_ASSET_STATUSES: Final[frozenset[IncidentStatus]] = frozenset(
    {
        IncidentStatus.ACTIVE_CONFIRMED,
        IncidentStatus.MONITORING,
        IncidentStatus.EXTINGUISHED,
    }
)
WITHHELD_MANIFEST_STATUSES: Final[frozenset[IncidentStatus]] = frozenset(IncidentStatus)


