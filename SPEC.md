# SPEC.md: AgentX Architecture & UI Upgrade

## 1. Objective
Upgrade the AgentX project from its current functional state to a "premium" orchestration framework by applying advanced coding patterns across the Python core and the TypeScript dashboard.

## 2. Core Features (The "Upgrade")

### Phase 1: Robust Core Data Layer (Python)
- **Tech**: Pydantic V2, Python 3.12+
- **Action**: Refactor `packages/agentx-core/agentx/planning/models.py` from standard dataclasses to Pydantic models.
- **Value**: Guaranteed runtime type safety, better error messages, and faster JSON serialization.

### Phase 2: Premium Dashboard Experience (React/TS)
- **Tech**: shadcn/ui, Tailwind CSS, Anime.js, Zustand
- **Action**: 
    - Replace generic HTML/CSS components with **shadcn/ui**.
    - Implement a centralized state store using **Zustand** in `apps/dashboard`.
    - Add subtle micro-animations using **Anime.js** for a premium feel.
- **Value**: Dramatic improvement in visual aesthetics and user interactivity.

### Phase 3: Hardened Security Gate (TS/Bash)
- **Tech**: Bash Scripting, Node.js
- **Action**: Refactor `apps/cli-ts/src/tools/bashTool.ts` to use safer execution patterns and improved sanitization logic.
- **Value**: Reduced risk of injection attacks and better cross-platform reliability on Windows/Linux.

## 3. Must-Haves
- All 86 Python tests MUST pass after the Pydantic refactor.
- Dashboard MUST maintain its current functionality while adopting new UI components.
- Zero regression in the Safety Gate performance.

## 4. Constraints
- Maintain the current monorepo structure.
- No breaking changes to the `agentx.json` configuration schema.
- All UI changes must be responsive.

## 5. Success Criteria
- [ ] Pytest suite returns 100% success with Pydantic models.
- [ ] Dashboard passes a visual "premium" audit.
- [ ] Bash tool correctly handles edge cases on Windows shell.
