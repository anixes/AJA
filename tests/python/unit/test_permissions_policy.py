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


def test_permission_engine_session_isolation():
    from aja.security.permissions import (
        set_current_permission_session,
        reset_current_permission_session,
    )

    PermissionEngine.clear_session_grants()
    engine = PermissionEngine(PermissionPolicy(scopes={"fs.write.global": "ask"}, ask_timeout_s=0))

    # Session 1: granted
    tok1 = set_current_permission_session("sess-1")
    PermissionEngine.add_session_grant("fs.write.global")
    res1 = engine.authorize("fs.write.global")
    assert res1.allowed is True
    reset_current_permission_session(tok1)

    # Session 2: NOT granted
    tok2 = set_current_permission_session("sess-2")
    res2 = engine.authorize("fs.write.global")
    assert res2.allowed is False
    assert res2.decision == "ask"
    reset_current_permission_session(tok2)


def test_permission_engine_deny_overrides_session_grant():
    PermissionEngine.clear_session_grants()
    # Explicit deny in policy
    engine = PermissionEngine(PermissionPolicy(scopes={"fs.write.global": "deny"}))

    # Even if a session grant was somehow registered
    PermissionEngine.add_session_grant("fs.write.global")

    # Authorize must evaluate deny first!
    res = engine.authorize("fs.write.global")
    assert res.allowed is False
    assert res.decision == "deny"


def test_permission_engine_grant_ttl_expiry():
    PermissionEngine.clear_session_grants()
    engine = PermissionEngine(PermissionPolicy(scopes={"fs.write.global": "ask"}, ask_timeout_s=0))

    # Add grant with negative TTL (already expired)
    PermissionEngine.add_session_grant("fs.write.global", ttl_s=-1.0)

    res = engine.authorize("fs.write.global")
    assert res.allowed is False
    assert res.decision == "ask"
