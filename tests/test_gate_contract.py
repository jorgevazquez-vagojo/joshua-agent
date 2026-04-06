"""Tests for GateVerdict Pydantic contract."""
import pytest
from pydantic import ValidationError

from joshua.gate_contract import GateVerdict, GATE_JSON_SCHEMA


class TestGateVerdict:
    def test_minimal_go(self):
        v = GateVerdict(verdict="GO")
        assert v.verdict == "GO"
        assert v.severity == "none"
        assert v.findings == ""
        assert v.issues == []
        assert v.recommended_action == ""
        assert v.confidence is None

    def test_full_verdict(self):
        v = GateVerdict(
            verdict="CAUTION",
            severity="medium",
            findings="Some issues found",
            issues=["issue 1", "issue 2"],
            recommended_action="Fix issues",
            confidence=0.85,
        )
        assert v.verdict == "CAUTION"
        assert v.severity == "medium"
        assert len(v.issues) == 2
        assert v.confidence == 0.85

    def test_revert_verdict(self):
        v = GateVerdict(verdict="REVERT", severity="critical", findings="Critical bug")
        assert v.verdict == "REVERT"
        assert v.severity == "critical"

    def test_invalid_verdict_raises(self):
        with pytest.raises(ValidationError):
            GateVerdict(verdict="SHIP_IT")

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            GateVerdict(verdict="GO", severity="catastrophic")

    def test_confidence_out_of_range_low(self):
        with pytest.raises(ValidationError):
            GateVerdict(verdict="GO", confidence=-0.1)

    def test_confidence_out_of_range_high(self):
        with pytest.raises(ValidationError):
            GateVerdict(verdict="GO", confidence=1.1)

    def test_confidence_boundary_values(self):
        assert GateVerdict(verdict="GO", confidence=0.0).confidence == 0.0
        assert GateVerdict(verdict="GO", confidence=1.0).confidence == 1.0

    def test_extra_fields_ignored(self):
        v = GateVerdict(verdict="GO", unknown_field="ignored")
        assert not hasattr(v, "unknown_field")

    def test_model_validate_from_dict(self):
        data = {"verdict": "GO", "severity": "low", "findings": "OK", "issues": [], "confidence": 0.9}
        v = GateVerdict.model_validate(data)
        assert v.verdict == "GO"
        assert v.confidence == 0.9

    def test_gate_json_schema_contains_required_fields(self):
        assert "verdict" in GATE_JSON_SCHEMA
        assert "severity" in GATE_JSON_SCHEMA
        assert "findings" in GATE_JSON_SCHEMA
        assert "issues" in GATE_JSON_SCHEMA
        assert "confidence" in GATE_JSON_SCHEMA
        assert "GO" in GATE_JSON_SCHEMA
        assert "CAUTION" in GATE_JSON_SCHEMA
        assert "REVERT" in GATE_JSON_SCHEMA
