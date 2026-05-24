import pytest
from aja.planning.models import PlanGraph, PlanNode, DoD
from aja.planning.dag_validator import DAGValidator

def test_dag_healer_empty_compound_node():
    """
    Test that an empty compound node is resolved by injecting a primitive no-op child.
    """
    compound = PlanNode(
        id="compound_1",
        task="A compound task with no children",
        node_type="compound",
        children=[]
    )
    graph = PlanGraph(goal="Test empty compound", nodes=[compound])
    
    # Pre-heal validation should fail on empty compound node children
    val_before = DAGValidator.validate(graph)
    assert not val_before.ok

    # Heal the graph
    healed_graph = DAGValidator.heal(graph)

    # Post-heal validation should pass
    val_after = DAGValidator.validate(healed_graph)
    assert val_after.ok

    # Verify that a no-op child was created and linked
    assert len(healed_graph.nodes) == 2
    noop_node = healed_graph.node_by_id("compound_1_noop")
    assert noop_node is not None
    assert noop_node.is_primitive
    assert "compound_1_noop" in compound.children


def test_dag_healer_primitive_with_children():
    """
    Test that a primitive node falsely configured with children has its children cleared.
    """
    primitive = PlanNode(
        id="primitive_1",
        task="A primitive task with fake children",
        node_type="primitive",
        children=["some_child"]
    )
    graph = PlanGraph(goal="Test primitive with children", nodes=[primitive])
    
    val_before = DAGValidator.validate(graph)
    assert not val_before.ok

    healed_graph = DAGValidator.heal(graph)
    
    val_after = DAGValidator.validate(healed_graph)
    assert val_after.ok
    assert not primitive.children


def test_dag_healer_compound_dependency_resolution():
    """
    Test that a dependency pointing to a compound node is re-routed to target descendant leaf primitives.
    """
    # compound_1 -> child_1 (primitive)
    # node_2 depends on compound_1
    child = PlanNode(id="child_1", task="Child Task", node_type="primitive")
    compound = PlanNode(
        id="compound_1",
        task="Compound Task",
        node_type="compound",
        children=["child_1"]
    )
    node_2 = PlanNode(
        id="node_2",
        task="Dependent Task",
        node_type="primitive",
        dependencies=["compound_1"]
    )
    
    graph = PlanGraph(goal="Test compound dependencies", nodes=[child, compound, node_2])
    
    val_before = DAGValidator.validate(graph)
    assert not val_before.ok

    healed_graph = DAGValidator.heal(graph)
    
    val_after = DAGValidator.validate(healed_graph)
    assert val_after.ok
    assert "child_1" in node_2.dependencies
    assert "compound_1" not in node_2.dependencies


def test_dag_healer_cycle_breaking():
    """
    Test that self-loops and multi-node cycles are broken by purging the cyclic back-edges.
    """
    # A -> B -> C -> A
    n_a = PlanNode(id="A", task="Task A", node_type="primitive", dependencies=["C"])
    n_b = PlanNode(id="B", task="Task B", node_type="primitive", dependencies=["A"])
    n_c = PlanNode(id="C", task="Task C", node_type="primitive", dependencies=["B"])
    
    graph = PlanGraph(goal="Test cycle breaking", nodes=[n_a, n_b, n_c])
    
    val_before = DAGValidator.validate(graph)
    assert not val_before.ok

    healed_graph = DAGValidator.heal(graph)
    
    val_after = DAGValidator.validate(healed_graph)
    assert val_after.ok


def test_dag_healer_unmet_precondition_propagation():
    """
    Test that unmet preconditions are resolved by propagating them upstream as effects.
    """
    # Node B expects state 'db_connected' = True.
    # Node A runs before B (B depends on A) but writes no effects.
    # The healer should inject 'db_connected': True into Node A's effects to satisfy B.
    n_a = PlanNode(id="A", task="Task A", node_type="primitive", dependencies=[])
    n_b = PlanNode(
        id="B",
        task="Task B",
        node_type="primitive",
        dependencies=["A"],
        preconditions={"db_connected": True}
    )
    
    graph = PlanGraph(goal="Test preconditions", nodes=[n_a, n_b])
    
    val_before = DAGValidator.validate(graph)
    assert not val_before.ok

    healed_graph = DAGValidator.heal(graph)
    
    val_after = DAGValidator.validate(healed_graph)
    assert val_after.ok
    assert n_a.effects.get("db_connected") is True
