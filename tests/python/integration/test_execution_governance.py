import pytest
import os
from aja.config_schema import ExecutionPolicy
from aja.runtime.execution.contracts import ExecutionRequest
from aja.runtime.execution.governance import GovernancePolicy, create_posix_preexec_fn


def test_governance_policy_clamps_timeout():
    policy = ExecutionPolicy(max_timeout=30.0)
    gov = GovernancePolicy(policy)
    
    # Request asks for 60s, policy max is 30s
    req = ExecutionRequest(command="sleep 10", timeout=60.0)
    limits = gov.apply(req)
    assert limits.timeout == 30.0
    
    # Request asks for 10s, policy max is 30s
    req = ExecutionRequest(command="sleep 10", timeout=10.0)
    limits = gov.apply(req)
    assert limits.timeout == 10.0


def test_governance_policy_clamps_memory():
    policy = ExecutionPolicy(max_memory="1g")
    gov = GovernancePolicy(policy)
    
    # Request asks for 2g, policy max is 1g
    req = ExecutionRequest(command="echo hi", memory="2g")
    limits = gov.apply(req)
    assert limits.memory_bytes == 1024 * 1024 * 1024
    assert limits.memory_str == "1g"
    
    # Request asks for 256m, policy max is 1g
    req = ExecutionRequest(command="echo hi", memory="256m")
    limits = gov.apply(req)
    assert limits.memory_bytes == 256 * 1024 * 1024
    assert limits.memory_str == "256m"
    
    # Request doesn't specify memory
    req = ExecutionRequest(command="echo hi", memory="")
    limits = gov.apply(req)
    assert limits.memory_bytes == 1024 * 1024 * 1024
    assert limits.memory_str == "1g"


def test_governance_policy_clamps_cpus():
    policy = ExecutionPolicy(max_cpus=2.0)
    gov = GovernancePolicy(policy)
    
    # Request asks for 4 cpus, max is 2
    req = ExecutionRequest(command="echo hi", cpus="4")
    limits = gov.apply(req)
    assert limits.cpus == 2.0
    
    # Request asks for 0.5 cpus, max is 2
    req = ExecutionRequest(command="echo hi", cpus="0.5")
    limits = gov.apply(req)
    assert limits.cpus == 0.5
    
    # Invalid CPU string falls back to max
    req = ExecutionRequest(command="echo hi", cpus="invalid")
    limits = gov.apply(req)
    assert limits.cpus == 2.0


def test_governance_policy_network():
    policy = ExecutionPolicy(allow_network_default=False)
    gov = GovernancePolicy(policy)
    
    req = ExecutionRequest(command="curl google.com", allow_network=True)
    limits = gov.apply(req)
    # Since default policy is False, it denies network
    assert limits.allow_network is False

    policy_allow = ExecutionPolicy(allow_network_default=True)
    gov_allow = GovernancePolicy(policy_allow)
    limits2 = gov_allow.apply(req)
    # Policy permits it, and request requested it
    assert limits2.allow_network is True


def test_governance_policy_force_docker():
    policy = ExecutionPolicy(force_docker=True)
    gov = GovernancePolicy(policy)
    
    req = ExecutionRequest(command="echo hi", use_docker=False)
    limits = gov.apply(req)
    assert limits.use_docker is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX specific logic")
def test_posix_preexec_fn():
    policy = ExecutionPolicy(max_memory="1g", max_timeout=30.0)
    gov = GovernancePolicy(policy)
    limits = gov.apply(ExecutionRequest(command="echo hi"))
    
    fn = create_posix_preexec_fn(limits)
    assert fn is not None
    assert callable(fn)
