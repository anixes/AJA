
import pytest
from agentx.planning.models import PlanGraph, PlanNode, DoD
from agentx.planning.verification import SerializabilityVerifier

def _make_node(id_str: str, preconditions=None, effects=None, deps=None) -> PlanNode:
    return PlanNode(
        id=id_str,
        task=f"Task {id_str}",
        node_type="primitive",
        dependencies=deps or [],
        preconditions=preconditions or {},
        effects=effects or {},
        dod=DoD("done", "deterministic"),
        uncertainty=0.1
    )

def test_serializability_rw_conflict():
    """
    Test a classic Read-Write conflict.
    Node A: Write X
    Node B: Read X, Write Y
    Node C: Write X (conflicts with B's Read and A's Write)
    """
    n_a = _make_node("A", effects={"x": 1})
    n_b = _make_node("B", preconditions={"x": 1}, effects={"y": 10}, deps=["A"])
    n_c = _make_node("C", effects={"x": 2}) # Should be serialized after A
    
    # Ready nodes: [A, C]. 
    # Conflict A -> C (Write-Write).
    # Batch 1: [A], Batch 2: [C].
    # Next wave: [B]. Precondition x=2 (from C) fails if B expects 1.
    
    graph = PlanGraph(goal="RW Conflict", nodes=[n_a, n_b, n_c])
    verifier = SerializabilityVerifier(graph)
    assert verifier.verify(iterations=3)

def test_serializability_transitive_conflict():
    """
    A -> B (Write X -> Read X)
    C (Write X)
    Even if C is independent of A, it conflicts with B.
    If C runs before A, B sees C's value.
    If C runs after B, B sees A's value.
    Sequential order (A, B, C) says B sees A's value, then C overwrites.
    """
    n_a = _make_node("A", effects={"x": "A_VAL"})
    n_b = _make_node("B", preconditions={"x": "A_VAL"}, effects={"out": "B_DONE"}, deps=["A"])
    n_c = _make_node("C", effects={"x": "C_VAL"})
    
    # Deterministic order: A, B, C.
    # Wave 1: [A, C]. Conflict A -> C.
    # Batch 1: [A], Batch 2: [C].
    # Wave 2: [B]. Precondition x="A_VAL". Actual x="C_VAL". B fails.
    
    graph = PlanGraph(goal="Transitive Conflict", nodes=[n_a, n_b, n_c])
    verifier = SerializabilityVerifier(graph)
    assert verifier.verify(iterations=3)

def test_serializability_circular_state_dependency():
    """
    Note: The HTN scheduler prevents logical cycles in dependencies.
    This tests state-level conflict cycles within a single wave.
    A: Read X, Write Y
    B: Read Y, Write X
    """
    n_a = _make_node("A", preconditions={"x": 0}, effects={"y": 1})
    n_b = _make_node("B", preconditions={"y": 0}, effects={"x": 1})
    
    # Sequential (A, B): 
    # A runs (x=0, y=1), B runs (y=1 != 0) -> B fails.
    
    graph = PlanGraph(goal="Circular State", nodes=[n_a, n_b])
    verifier = SerializabilityVerifier(graph)
    # Our batching should separate them because of A.effects & B.preconditions
    assert verifier.verify(iterations=3)

def test_stress_large_random_wave():
    """
    Test many nodes in a single wave with overlapping conflicts.
    """
    nodes = []
    # 10 nodes all writing to the same key 'v'
    for i in range(10):
        nodes.append(_make_node(f"N{i}", effects={"v": i}))
    
    graph = PlanGraph(goal="Large Conflict Wave", nodes=nodes)
    verifier = SerializabilityVerifier(graph)
    # This should result in 10 batches of 1 node each.
    assert verifier.verify(iterations=1)

def test_interleaved_dependencies_and_conflicts():
    """
    Verify that explicit dependencies and state conflicts combine correctly.
    """
    # A -> B (dep)
    # C -> D (dep)
    # B and C conflict on key 'k'
    n_a = _make_node("A", effects={"a": 1})
    n_b = _make_node("B", preconditions={"a": 1}, effects={"k": "B_VAL"}, deps=["A"])
    n_c = _make_node("C", effects={"k": "C_VAL"})
    n_d = _make_node("D", preconditions={"k": "C_VAL"}, effects={"d": 1}, deps=["C"])
    
    # Seq (A, B, C, D):
    # Wave 1: [A, C]. Conflict A, C ? No. Batch 1: [A, C]. State: {a:1, k:C_VAL}
    # Wave 2: [B, D]. 
    # B: precond a=1 (ok), effects k=B_VAL. State: {a:1, k:B_VAL, d:1}
    # D: precond k=C_VAL (FAILED because B overwrote it).
    
    graph = PlanGraph(goal="Interleaved", nodes=[n_a, n_b, n_c, n_d])
    verifier = SerializabilityVerifier(graph)
    assert verifier.verify(iterations=5)
