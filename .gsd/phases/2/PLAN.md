---
phase: 2
plan: 1
wave: 1
depends_on: []
files_modified: ["apps/dashboard/package.json"]
autonomous: true
user_setup: []
must_haves:
  truths:
    - "Zustand and Anime.js are installed in dashboard dependencies"
  artifacts:
    - "apps/dashboard/package.json updated"
---

# Plan 2.1: Setup Frontend Utilities (Zustand & Anime.js)

<objective>
Install and configure the core utilities for state management and high-performance animations in the dashboard.
</objective>

<context>
- apps/dashboard/package.json
</context>

<tasks>

<task type="auto">
  <name>Install zustand and animejs</name>
  <files>apps/dashboard/package.json</files>
  <action>
    - Run `npm install zustand animejs` in `apps/dashboard`.
    - Also install `@types/animejs` as a dev dependency.
    AVOID: Installing in the root directory; use the dashboard app directory.
  </action>
  <verify>grep "zustand" apps/dashboard/package.json && grep "animejs" apps/dashboard/package.json</verify>
  <done>Dependencies added to package.json.</done>
</task>

<task type="auto">
  <name>Initialize Zustand store</name>
  <files>apps/dashboard/src/store/useStore.ts</files>
  <action>
    - Create a basic Zustand store to manage the dashboard's active tab and data state.
    - Refactor `activeTab` and `data` from `App.tsx` local state into this store later.
  </action>
  <verify>ls apps/dashboard/src/store/useStore.ts</verify>
  <done>Store file created with initial schema.</done>
</task>

</tasks>

<verification>
- [ ] Dependencies verified in package.json.
- [ ] useStore.ts exists and compiles.
</verification>

<success_criteria>
- [ ] All tasks verified
- [ ] Must-haves confirmed
</success_criteria>
