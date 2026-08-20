"""CF-06 renewal case generation — one case per AMS expiration, four stages."""

from __future__ import annotations

from dataclasses import dataclass

RENEWAL_STAGES = ("90-day", "60-day", "30-day", "Completion")


@dataclass(frozen=True)
class RenewalIdentity:
    policy_number: str
    expiration_date: str  # ISO date
    stage: str

    @property
    def case_key(self) -> str:
        return f"REN|{self.policy_number}|{self.expiration_date}"


def renewal_stage_for_days_out(days_out: int) -> str:
    """Map days-to-expiration onto the single-case stage. Never opens extra tickets."""
    if days_out >= 75:
        return "90-day"
    if days_out >= 45:
        return "60-day"
    if days_out >= 1:
        return "30-day"
    return "Completion"


def renewal_identity(policy_number: str, expiration_date: str, *, days_out: int) -> RenewalIdentity:
    if not policy_number or not expiration_date:
        raise ValueError("policy_number and expiration_date are required for a renewal case")
    return RenewalIdentity(
        policy_number=policy_number,
        expiration_date=expiration_date,
        stage=renewal_stage_for_days_out(days_out),
    )
