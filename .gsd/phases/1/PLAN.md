---
phase: 1
plan: 1
wave: 1
depends_on: []
files_modified: ["packages/agentx-core/agentx/planning/models.py"]
autonomous: true
must_haves:
  truths:
    - "PlanNode and PlanGraph use Pydantic BaseModel for validation"
    - "Serialization/Deserialization works via model_dump and model_validate"
    - "All existing field constraints (uncertainty, strategy, node_type) are enforced via Pydantic validators"
  artifacts:
    - "packages/agentx-core/agentx/planning/models.py is refactored"
---

# Plan 1.1: Pydantic Refactor of Core Models

<objective>
Refactor the core planning models from standard dataclasses to Pydantic V2 models to improve type-safety, validation, and serialization performance.
</objective>

<context>
- packages/agentx-core/agentx/planning/models.py
</context>

<tasks>

<task type="auto">
  <name>Refactor DoD, PlanNode, and PlanGraph to Pydantic</name>
  <files>packages/agentx-core/agentx/planning/models.py</files>
  <action>
    - Import `BaseModel`, `Field`, and `field_validator` from `pydantic`.
    - Convert `DoD`, `PlanNode`, `PlanGraph`, and `PlanVersion` to inherit from `BaseModel`.
    - Replace `field(default_factory=...)` with `Field(default_factory=...)`.
    - Implement `field_validator` for `validation_type`, `strategy`, `node_type`, and `uncertainty`.
    - Remove manual `to_dict` and `from_dict` methods as Pydantic provides `model_dump` and `model_validate`.
    - Ensure `iso_timestamp` property remains available.
    - Keep `to_json` and `from_json` helpers but back them with Pydantic's native methods.
    AVOID: Using `pydantic.v1` models; use Pydantic V2 (`pydantic.BaseModel`).
  </action>
  <verify>pytest tests/python/test_models.py (if exists) or run a simple validation script</verify>
  <done>Models are Pydantic-based and pass validation checks.</done>
</task>

<task type="auto">
  <name>Verify integrity with existing test suite</name>
  <files>tests/python/test_models.py</files>
  <action>
    Run the unified test suite to ensure the refactor didn't break existing logic.
  </action>
  <verify>npm run test:py</verify>
  <done>All 86 Python tests pass.</done>
</task>

</tasks>

<verification>
- [ ] DoD validation correctly rejects invalid types.
- [ ] PlanNode validation correctly rejects invalid strategies.
- [ ] PlanGraph correctly serializes/deserializes complex nested structures.
- [ ] Pytest suite returns 100% success.
</verification>

<success_criteria>
- [ ] All tasks verified
- [ ] Must-haves confirmed
</success_criteria>
