"""Strict Pydantic contract for gate agent verdict output.

Gate agents must output a JSON block matching GateVerdict.
The parser in sprint.py validates against this model — any field
mismatch produces a clear warning and falls back to CAUTION.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class GateVerdict(BaseModel):
    """Structured output contract for gate agents.

    JSON schema (include this block in gate agent prompts):

        {
            "verdict": "GO" | "CAUTION" | "REVERT",
            "severity": "none" | "low" | "medium" | "high" | "critical",
            "findings": "narrative summary of what was reviewed",
            "issues": ["specific issue 1", "specific issue 2"],
            "recommended_action": "what the dev agent should do next",
            "confidence": 0.85
        }
    """

    verdict: Literal["GO", "CAUTION", "REVERT"]
    severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    findings: str = ""
    issues: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    model_config = {"extra": "ignore"}  # ignore unknown fields from LLM output


# JSON schema snippet to embed in gate agent prompts
GATE_JSON_SCHEMA = """\
Output your verdict as a JSON block:

```json
{
    "verdict": "GO",
    "severity": "none",
    "findings": "Brief narrative of what was reviewed and the overall quality.",
    "issues": ["Specific issue found", "Another issue if any"],
    "recommended_action": "What the dev agent should focus on next cycle.",
    "confidence": 0.9
}
```

verdict    : GO (ship it), CAUTION (flag issues, continue), REVERT (roll back)
severity   : none | low | medium | high | critical
findings   : 1-3 sentence narrative
issues     : list of specific problems found (empty list if none)
confidence : 0.0–1.0, your confidence in this verdict (optional)

Also output EFFORT: <1-5> on a separate line where:
1 = trivial (typos, formatting)
2 = minor (small logic fix)
3 = moderate (feature work, refactor)
4 = significant (architecture change, multiple files)
5 = major (core system rewrite, security critical)
"""
