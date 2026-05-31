"""Skill Curator (report-only): age report for .claude/skills.

Scans the committed Claude Code skills under ``.claude/skills`` and reports
how long each has gone without a git modification. Skills past the "stale"
threshold (default 30 days) and the "review" threshold (default 90 days) are
flagged for a *human* to look at.

This job NEVER deletes, moves, or edits skill files. The skills in this repo
are hand-authored and version-controlled; git already handles their history.
The original "auto-deletion after 90 days" concept is deliberately not
implemented — silently removing committed business logic is unsafe. Staleness
here means "last modified in git," not "last used at runtime"; Hermes has no
per-skill usage telemetry to read from.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Defaults match the original concept's thresholds, overridable via env.
DEFAULT_STALE_DAYS = int(os.environ.get("HERMES_SKILL_STALE_DAYS", "30"))
DEFAULT_REVIEW_DAYS = int(os.environ.get("HERMES_SKILL_REVIEW_DAYS", "90"))

_FRESH = "fresh"
_STALE = "stale"
_REVIEW = "review"


@dataclass
class SkillAge:
    """One skill and how long since it was last touched in git."""

    name: str
    path: str
    last_modified: datetime | None
    age_days: int | None
    status: str  # _FRESH | _STALE | _REVIEW | "unknown"


@dataclass
class CuratorResult:
    ok: bool
    message: str
    skills: list[SkillAge] = field(default_factory=list)
    stale_days: int = DEFAULT_STALE_DAYS
    review_days: int = DEFAULT_REVIEW_DAYS
    warnings: list[str] = field(default_factory=list)

    def format_lines(self) -> list[str]:
        """Human-readable report lines."""
        lines = [
            "Hermes Skill Curator — report only (no files are deleted or changed)",
            f"Thresholds: stale > {self.stale_days}d, review > {self.review_days}d "
            "(age = days since last git modification)",
            "",
        ]
        if not self.skills:
            lines.append("No skills found under .claude/skills.")
            return lines

        review = [s for s in self.skills if s.status == _REVIEW]
        stale = [s for s in self.skills if s.status == _STALE]
        fresh = [s for s in self.skills if s.status == _FRESH]
        unknown = [s for s in self.skills if s.status == "unknown"]

        def _fmt(s: SkillAge) -> str:
            age = f"{s.age_days}d" if s.age_days is not None else "unknown"
            return f"  • {s.name} — {age}"

        if review:
            lines.append(f":mag: Review candidates (> {self.review_days}d): {len(review)}")
            lines.extend(_fmt(s) for s in review)
            lines.append("  ↳ Have a human confirm these are obsolete before deleting via git.")
            lines.append("")
        if stale:
            lines.append(f":hourglass: Stale (> {self.stale_days}d): {len(stale)}")
            lines.extend(_fmt(s) for s in stale)
            lines.append("")
        lines.append(f":white_check_mark: Fresh: {len(fresh)}   |   Unknown age: {len(unknown)}")
        if self.warnings:
            lines.append("")
            lines.append(":warning: " + "; ".join(self.warnings))
        return lines


def _repo_root() -> Path:
    """Resolve the git repo root, falling back to the package parent."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return Path(__file__).resolve().parents[2]


def _last_git_modified(repo_root: Path, rel_path: str) -> datetime | None:
    """Return the last commit date that touched ``rel_path``, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%cI", "--", rel_path],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    stamp = out.stdout.strip()
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone(timezone.utc)
    except ValueError:
        return None


def run(
    *,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    review_days: int = DEFAULT_REVIEW_DAYS,
) -> CuratorResult:
    """Build the skill-age report. Read-only — touches no skill files."""
    current = now or datetime.now(timezone.utc)
    repo_root = _repo_root()
    skills_dir = repo_root / ".claude" / "skills"

    if not skills_dir.is_dir():
        return CuratorResult(
            ok=False,
            message=f"Skills directory not found: {skills_dir}",
            stale_days=stale_days,
            review_days=review_days,
        )

    skills: list[SkillAge] = []
    warnings: list[str] = []

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        rel_path = skill_md.relative_to(repo_root).as_posix()
        last_mod = _last_git_modified(repo_root, rel_path)

        if last_mod is None:
            warnings.append(f"{name}: no git history (uncommitted or untracked)")
            skills.append(SkillAge(name, rel_path, None, None, "unknown"))
            continue

        age_days = (current - last_mod).days
        if age_days > review_days:
            status = _REVIEW
        elif age_days > stale_days:
            status = _STALE
        else:
            status = _FRESH
        skills.append(SkillAge(name, rel_path, last_mod, age_days, status))

    review_n = sum(1 for s in skills if s.status == _REVIEW)
    stale_n = sum(1 for s in skills if s.status == _STALE)
    message = (
        f"Scanned {len(skills)} skill(s): "
        f"{review_n} review candidate(s), {stale_n} stale, "
        f"{len(skills) - review_n - stale_n} fresh/unknown."
    )

    return CuratorResult(
        ok=True,
        message=message,
        skills=skills,
        stale_days=stale_days,
        review_days=review_days,
        warnings=warnings,
    )
