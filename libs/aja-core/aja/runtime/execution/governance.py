import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from aja.config import CONFIG
from aja.config_schema import ExecutionPolicy
from aja.runtime.execution.contracts import ExecutionRequest


@dataclass
class BoundedExecutionLimits:
    timeout: float
    memory_bytes: Optional[int]
    memory_str: str
    cpus: float
    allow_network: bool
    use_docker: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeout": self.timeout,
            "memory_bytes": self.memory_bytes,
            "memory_str": self.memory_str,
            "cpus": self.cpus,
            "allow_network": self.allow_network,
            "use_docker": self.use_docker,
        }


class GovernancePolicy:
    """Enforces global resource boundaries against individual execution requests."""

    def __init__(self, policy: Optional[ExecutionPolicy] = None):
        self.policy = policy or CONFIG.execution_policy

    def apply(self, request: ExecutionRequest) -> BoundedExecutionLimits:
        """Clamps the execution request against the global execution policy."""
        # 1. Timeout
        timeout = min(request.timeout, self.policy.max_timeout)

        # 2. Memory
        req_mem_bytes = self.parse_memory(request.memory)
        pol_mem_bytes = self.parse_memory(self.policy.max_memory)
        
        if req_mem_bytes and pol_mem_bytes:
            applied_mem_bytes = min(req_mem_bytes, pol_mem_bytes)
        elif pol_mem_bytes:
            applied_mem_bytes = pol_mem_bytes
        else:
            applied_mem_bytes = req_mem_bytes

        # Format applied memory back to string for Docker / human reading
        applied_mem_str = self.format_memory(applied_mem_bytes) if applied_mem_bytes else request.memory

        # 3. CPUs
        # Parse CPU strings (could be "0.5", "1") to floats
        try:
            req_cpus = float(request.cpus)
        except ValueError:
            req_cpus = self.policy.max_cpus

        applied_cpus = min(req_cpus, self.policy.max_cpus)

        # 4. Network
        # If policy defaults to False, network is denied unless request explicitly requires it 
        # (Though we might want a hard clamp here if policy says `allow_network_max: False`).
        # For now, if the policy sets allow_network_default=False, we only allow it if the system isn't globally blocking it.
        # Currently the config schema doesn't have a hard block, so we take the request but default to policy if not specified.
        # (Assuming the request explicitly overrides if needed, but in strict environments, a hard block might be added).
        allow_network = request.allow_network and self.policy.allow_network_default

        # 5. Docker
        use_docker = request.use_docker or self.policy.force_docker

        return BoundedExecutionLimits(
            timeout=timeout,
            memory_bytes=applied_mem_bytes,
            memory_str=applied_mem_str,
            cpus=applied_cpus,
            allow_network=allow_network,
            use_docker=use_docker,
        )

    @staticmethod
    def parse_memory(mem_str: str) -> Optional[int]:
        """Parses memory strings like '256m', '1g' into bytes."""
        if not mem_str:
            return None
        match = re.match(r"^(\d+)([kmgKMG])?$", mem_str.strip())
        if not match:
            return None
        val = int(match.group(1))
        unit = (match.group(2) or "").lower()
        if unit == "k":
            return val * 1024
        elif unit == "m":
            return val * 1024 * 1024
        elif unit == "g":
            return val * 1024 * 1024 * 1024
        return val

    @staticmethod
    def format_memory(mem_bytes: int) -> str:
        if mem_bytes >= 1024 * 1024 * 1024 and mem_bytes % (1024 * 1024 * 1024) == 0:
            return f"{mem_bytes // (1024 * 1024 * 1024)}g"
        if mem_bytes >= 1024 * 1024 and mem_bytes % (1024 * 1024) == 0:
            return f"{mem_bytes // (1024 * 1024)}m"
        if mem_bytes >= 1024 and mem_bytes % 1024 == 0:
            return f"{mem_bytes // 1024}k"
        return f"{mem_bytes}b"


def create_posix_preexec_fn(limits: BoundedExecutionLimits):
    """
    Creates a preexec_fn for Unix systems to enforce resource limits via `resource` module.
    Will safely no-op on Windows.
    """
    if os.name != "posix":
        return None

    def preexec():
        try:
            import resource
            if limits.memory_bytes:
                # RLIMIT_AS (Address space) limits the total virtual memory available to the process
                # We disable this because 256MB address space causes Python 3.12 to instantly crash on macOS/Linux
                # soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                # resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, hard))
                pass
            
            # CPUs: In pure Python without cgroups, we can't easily restrict CPU *cores*, 
            # but we can restrict CPU *time* (RLIMIT_CPU in seconds). 
            # We don't map `cpus: float` directly to RLIMIT_CPU because it's not a 1:1 map, 
            # but we could enforce the timeout as a CPU time limit as a secondary fail-safe.
            if limits.timeout > 0:
                cpu_seconds = max(1, int(limits.timeout))
                soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, hard))
        except Exception:
            # Gracefully degrade if permissions or OS don't support it
            pass

    return preexec
