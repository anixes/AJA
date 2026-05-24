# Deprecation and Compatibility Policy

As AJA transitions from an experimental AI project into a maintained runtime platform, we enforce strict policies regarding API drift and legacy containment.

## 1. Runtime API Stability
The `LanceRuntimeTaskStore`, `CronScheduler`, and `RuntimeEventSink` are considered stable Public APIs.
- Breaking changes to these interfaces require a deprecation warning cycle.
- Database schema changes (e.g., in Pydantic models in `config_schema.py`) must include a migration strategy so existing tasks are not orphaned.

## 2. Legacy Containment Philosophy
Because AJA evolved from a chatbot-style assistant, there are legacy LLM "persona" prompts and tight coupling artifacts scattered in the repository.
- We do not hard-delete legacy endpoints immediately if they are actively used by older clients.
- Instead, we **box them** into the `aja/gateway/` adapters layer or explicitly mark them with `@deprecated`.
- New runtime features must never rely on legacy presentation surfaces.

## 3. Worker Baton Compatibility
The Apache Arrow schema used in `BatonManager` is currently brittle. If the schema changes (e.g., adding new fields to `MissionBatonPayload`), workers running on older versions will fail to deserialize the baton.
- Until we implement versioned baton schemas, coordinate updates across all distributed workers simultaneously.

## 4. Documentation Archiving
When architectural components are completely replaced (e.g., the old Phase 1-28 planning docs), they are not deleted. They are moved to `docs/archive/` to preserve historical design context. Future contributors use this archive to understand *why* certain runtime decisions were made.
