from aja.security.permissions import PermissionEngine, PermissionPolicy


def test_permission_policy_wildcard_and_deny_precedence():
    policy = PermissionPolicy(
        scopes={
            "mcp.github.*": "allow",
            "mcp.github.delete_repo": "deny",
        }
    )

    assert policy.decision_for("mcp.github.list_issues") == ("allow", "mcp.github.*")
    assert policy.decision_for("mcp.github.delete_repo") == ("deny", "mcp.github.delete_repo")


def test_permission_policy_ask_timeout_defaults_to_deny():
    engine = PermissionEngine(PermissionPolicy(scopes={"desktop.interact": "ask"}, ask_timeout_s=0))

    result = engine.authorize("desktop.interact", dry_run=False)

    assert result.allowed is False
    assert result.decision == "ask"


def test_permission_policy_grants_with_provider():
    engine = PermissionEngine(
        PermissionPolicy(scopes={"browser.navigate": "ask"}, ask_timeout_s=1),
        approval_provider=lambda scope, reason, timeout: True,
    )

    result = engine.authorize("browser.navigate")

    assert result.allowed is True
    assert result.grant_id


def test_permission_policy_unknown_scope_denies():
    engine = PermissionEngine(PermissionPolicy(scopes={"python.*": "allow"}))

    result = engine.authorize("desktop.interact")

    assert result.allowed is False
    assert result.decision == "deny"
