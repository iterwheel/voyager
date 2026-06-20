"""Governed PR review-fix bot constants."""

from __future__ import annotations

from voyager.bots.assembly.constants import ASSEMBLY_AGENT_SLUG

REVIEW_FIX_AGENT_SLUG = ASSEMBLY_AGENT_SLUG
REVIEW_FIX_AGENT_ID = "governed-pr-review-fix"
REVIEW_FIX_KIND = "review_fix_loop"
REVIEW_FIX_DYNAMIC = "review_fix_loop"
REVIEW_FIX_COMMANDS: tuple[str, ...] = ("/review-fix", "/pr-review-fix")
REVIEW_FIX_COMMENT_MARKER = "<!-- iterwheel:review-fix-loop -->"
