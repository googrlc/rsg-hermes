"""Hermes skills catalog.

A single place that enumerates what Hermes can do, so it can list its own
capabilities and we have a registry to grow. Two layers:

- runtime tools: the functions the conversational agent can actually call right now
  (search, lookups, reports, renewals, web research, intake, etc.) — read live from
  nl_agent so the list never drifts from reality.
- domain skills: the richer playbooks under .claude/skills (carrier appetite,
  renewal review, proposal builder, deep research, …) — read from their SKILL.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SKILLS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "skills"


def runtime_tools() -> list[dict[str, str]]:
    """The agent's live, executable tools (name + one-line description)."""
    from hermes.core.nl_agent import _TOOLS

    out: list[dict[str, str]] = []
    for tool in _TOOLS:
        fn = tool.get("function", {})
        desc = (fn.get("description") or "").strip()
        out.append({"name": fn.get("name", "?"), "description": desc.split(". ")[0][:160]})
    return out


def _skill_description(skill_dir: Path) -> str:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return ""
    try:
        text = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    if m:
        return m.group(1).strip().strip('"').strip("'")[:200]
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("---", "#", "name:")):
            return line[:200]
    return ""


def domain_skills() -> list[dict[str, str]]:
    """The .claude/skills playbooks (name + description)."""
    if not _SKILLS_DIR.is_dir():
        return []
    skills: list[dict[str, str]] = []
    for child in sorted(_SKILLS_DIR.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            skills.append({"name": child.name, "description": _skill_description(child)})
    return skills


def catalog() -> dict[str, Any]:
    tools = runtime_tools()
    skills = domain_skills()
    return {
        "runtime_tools": tools,
        "domain_skills": skills,
        "counts": {"runtime_tools": len(tools), "domain_skills": len(skills)},
    }


def render_text() -> str:
    """Human-readable capability list for the agent to speak."""
    cat = catalog()
    lines = ["Here's what I can do right now:", "", "**Live tools I can run:**"]
    for t in cat["runtime_tools"]:
        lines.append(f"• {t['name']} — {t['description']}")
    if cat["domain_skills"]:
        lines.append("")
        lines.append(f"**Domain playbooks ({cat['counts']['domain_skills']}):**")
        for s in cat["domain_skills"]:
            lines.append(f"• {s['name']} — {s['description']}")
    return "\n".join(lines)
