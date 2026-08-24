# Example 04: Analyze a Repo's Test Coverage

**Capability**: Code analysis mission — shell tools + search over a local workspace
**Difficulty**: Intermediate
**Prerequisites**: A configured LLM provider; a target repository AJA is allowed to read (workspace root or `allow_out_of_bounds_paths` enabled)

## Objective

Have AJA audit which source modules lack test coverage and produce a prioritized report.

## Steps

From your project root:

```bash
aja run "Analyze this repository's test coverage: list source files that have no matching test file, identify the 5 most critical untested modules, and suggest what tests to write first. Show file paths."
```

Or explore interactively:

```bash
aja chat
```
```
Which Python modules in this repo have no corresponding test file?
```

## Expected Output

A report listing untested modules with absolute/relative paths, a prioritized top-5 with reasoning (e.g. security- or business-critical code first), and concrete test suggestions. All shell commands used are visible in the mission journal.

## How It Works

The worker uses guarded shell tools (`ls`/directory listings) and content search to map `src/` files against test directories, then reasons over the structure. Every command passes through CommandGuard classification — read-only commands run directly; anything destructive would be denied or require approval.

## Troubleshooting

- **"Path outside workspace" denials**: keep the repo inside the workspace root or enable `allow_out_of_bounds_paths` in settings.
- **Shallow analysis**: ask for specific evidence ("show the import graph") to push deeper tool use.
- **Tests flagged as missing but they exist**: ensure naming conventions match (AJA matches `test_*.py` patterns); clarify in the prompt.
