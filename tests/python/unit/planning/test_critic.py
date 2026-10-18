import json
import pytest
from unittest.mock import patch, MagicMock
from aja.decision.critic import critique_plan, llm_critique, deep_critique
from aja.planning.models import PlanGraph, PlanNode

def test_rule_based_critique_missing_precondition():
    """Test rule-based critique on plan graph with missing preconditions."""
    # Create simple node with precondition
    node = PlanNode(
        id="n1",
        task="Read target file",
        dependencies=[],
        strategy="direct",
        inputs=[],
        outputs={},
        preconditions={"file_exists": "true"},
        effects={}
    )
    plan = PlanGraph(goal="Test goal", nodes=[node])
    
    # State does not contain "file_exists" -> should report missing precondition, logic gap, and no_effect
    critique = critique_plan(plan, state={})
    assert critique["severity"] == 3  
    issues = {i["type"] for i in critique["issues"]}
    assert "missing_precondition" in issues
    assert "logic_gap" in issues
    assert "no_effect" in issues

def test_llm_critique_with_cot_pretext():
    """Test that llm_critique robustly parses JSON even when the LLM outputs reasoning pre-text."""
    # Mock LLM response with chain-of-thought prefix and markdown fences
    mock_response = """
Here is my step-by-step thinking:
1. The node 'n1' requires 'file_exists' but the state is empty.
2. The user has not provided dependencies to check.
This represents a logic gap and a hidden assumption.

```json
{
  "issues": [
    {"type": "logic_gap", "node": "n1", "detail": "Hidden assumption about file state."}
  ],
  "severity": 2
}
```
"""
    node = PlanNode(
        id="n1",
        task="Read target file",
        dependencies=[],
        strategy="direct",
        inputs=[],
        outputs={},
        preconditions={},
        effects={}
    )
    plan = PlanGraph(goal="Test goal", nodes=[node])
    
    with patch("aja.llm.completion", return_value=mock_response):
        critique = llm_critique(plan, state={})
        
    assert critique["severity"] == 2
    assert len(critique["issues"]) == 1
    assert critique["issues"][0]["type"] == "logic_gap"
    assert critique["issues"][0]["node"] == "n1"

def test_deep_critique_fast_fail():
    """Test that deep_critique fast-fails on severe rule-based issues without calling LLM."""
    node1 = PlanNode(id="n1", task="Task 1", preconditions={"k": "v"}, dependencies=[], effects={})
    node2 = PlanNode(id="n2", task="Task 2", preconditions={"k2": "v2"}, dependencies=[], effects={})
    plan = PlanGraph(goal="Test goal", nodes=[node1, node2])
    
    # This will trigger 4 issues total (2 missing preconditions + 2 logic gaps)
    # Since severity >= 2, deep_critique should immediately return rule_crit without LLM call.
    with patch("aja.decision.critic.llm_critique") as mock_llm:
        result = deep_critique(plan, state={})
        assert mock_llm.call_count == 0
        assert result["severity"] >= 2
