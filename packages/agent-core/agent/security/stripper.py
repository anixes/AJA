import json
import re
import shlex
import sys


class CommandStripper:
    """
    Lightweight shell command normalizer used by the TypeScript bash tool.
    It is intentionally heuristic, but surfaces enough structure to support
    allow / ask / deny decisions.
    """

    SAFE_WRAPPERS = {"sudo", "nice", "timeout", "time", "nohup", "stdbuf", "watch"}
    BLOCKED_ENV_VARS = {
        "PATH",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "NODE_OPTIONS",
        "RUBYOPT",
        "BASH_ENV",
        "ENV",
        "PROMPT_COMMAND",
    }
    OPERATOR_PATTERN = re.compile(r"(\|\||&&|[|;><`])")

    def __init__(self, command: str):
        self.original = command.strip()
        self.env_vars = {}
        self.blocked_env_vars = {}
        self.wrappers = []
        self.root_command = ""
        self.args = []
        self.operators = []
        self.dangerous_patterns = []
        self.command_count = 0

    def strip(self):
        self.operators = [match for match in self.OPERATOR_PATTERN.findall(self.original)]
        self.command_count = self._estimate_command_count()
        self._detect_dangerous_patterns()

        if not self.original:
            return

        try:
            parts = shlex.split(self.original, posix=True)
        except ValueError:
            parts = self.original.split()
            self.dangerous_patterns.append("unbalanced-shell-syntax")

        if not parts:
            return

        i = 0
        while i < len(parts) and self._looks_like_env_var(parts[i]):
            key, val = parts[i].split("=", 1)
            if key in self.BLOCKED_ENV_VARS:
                self.blocked_env_vars[key] = val
            else:
                self.env_vars[key] = val
            i += 1

        while i < len(parts) and parts[i].lower() in self.SAFE_WRAPPERS:
            wrapper = parts[i].lower()
            self.wrappers.append(wrapper)
            i += 1
            if wrapper in {"timeout", "nice", "stdbuf", "watch"}:
                while i < len(parts) and (parts[i].startswith("-") or self._looks_like_wrapper_value(parts[i])):
                    i += 1

        if i < len(parts):
            self.root_command = parts[i]
            self.args = parts[i + 1 :]

        self._inspect_arguments()

    def report(self):
        return {
            "Original": self.original,
            "Env Vars": self.env_vars,
            "Blocked Env Vars": self.blocked_env_vars,
            "Wrappers": self.wrappers,
            "Root Binary": self.root_command,
            "Arguments": " ".join(self.args),
            "Argument Tokens": self.args,
            "Operators": self.operators,
            "Dangerous Patterns": sorted(set(self.dangerous_patterns)),
            "Command Count": self.command_count,
        }

    def _looks_like_env_var(self, token: str) -> bool:
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token)) and not token.startswith("-")

    def _looks_like_wrapper_value(self, token: str) -> bool:
        return bool(re.match(r"^\d+[smhd]?$", token))

    def _estimate_command_count(self) -> int:
        separators = re.findall(r"(?:\|\||&&|[|;])", self.original)
        return 1 + len(separators) if self.original else 0

    def _detect_dangerous_patterns(self):
        checks = {
            "command-substitution": [r"\$\(", r"`"],
            "network-pipe": [r"\bcurl\b.*\|\s*(bash|sh|zsh|python|node|pwsh|powershell)\b", r"\bwget\b.*\|\s*(bash|sh|zsh|python|node|pwsh|powershell)\b", r"\bInvoke-WebRequest\b.*\|\s*(bash|sh|pwsh|powershell)\b"],
            "ssh-write": [r">+\s*~?/?\.ssh/", r"authorized_keys", r"known_hosts"],
            "system-path-write": [r">+\s*/etc/", r">+\s*/usr/", r">+\s*[A-Za-z]:\\Windows\\System32", r">+\s*[A-Za-z]:\\Program Files"],
            "recursive-delete-flag": [r"\s-rf?\b", r"\s/fr\b"],
        }

        for label, patterns in checks.items():
            for pattern in patterns:
                if re.search(pattern, self.original, flags=re.IGNORECASE):
                    self.dangerous_patterns.append(label)
                    break

    def _inspect_arguments(self):
        joined = " ".join(self.args)
        if re.search(r"(^|\s)\.\.(?:/|\\)", joined):
            self.dangerous_patterns.append("path-traversal")

        protected_targets = [
            r"(^|/)\.git($|/)",
            r"(^|/)\.ssh($|/)",
            r"(^|/)(etc|usr|bin|sbin|var)(/|$)",
            r"(^|\\)Windows(\\|$)",
            r"System32",
        ]
        for pattern in protected_targets:
            if re.search(pattern, joined, flags=re.IGNORECASE):
                self.dangerous_patterns.append("protected-path")
                break


if __name__ == "__main__":
    test_cmd = sys.argv[1] if len(sys.argv) > 1 else "DEBUG=true sudo timeout 10s npm install"
    stripper = CommandStripper(test_cmd)
    stripper.strip()
    print(json.dumps(stripper.report(), indent=2))
