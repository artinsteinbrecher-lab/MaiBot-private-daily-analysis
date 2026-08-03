"""Shared limits for the fact-oriented analysis pipeline.

Presentation and entertainment settings intentionally do not live here. The
module remains as a compatibility import for hosts or extensions that used
AnalysisConfig in older releases.
"""


class AnalysisConfig:
    """Stable, non-entertainment analysis limits."""

    MAX_REASON_LENGTH: int = 240
    MAX_TITLE_LENGTH: int = 80
    MAX_CLAIM_LENGTH: int = 240
    MAX_EVIDENCE_IDS_PER_CLAIM: int = 8
    MAX_EVIDENCE_IDS_PER_EVENT: int = 24
