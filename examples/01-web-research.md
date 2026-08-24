# Example 01: Research the Current Stable Python Version

**Capability**: Autonomous web research — search_web + fetch_url with cited summary
**Difficulty**: Beginner
**Prerequisites**: AJA installed (`aja doctor` passes) and a configured LLM provider

## Objective

Ask AJA to find the current stable Python release and report back with sources — no manual browsing.

## Steps

```bash
aja run "Find the current stable version of Python from python.org and summarize what's new in it. Cite your sources."
```

Prefer to simulate first? Add `--dry-run`:

```bash
aja run "Find the current stable version of Python" --dry-run
```

Or do it conversationally:

```bash
aja chat
```
then type:
```
What's the latest stable Python version? Check python.org.
```

## Expected Output

A synthesized answer naming the stable version, key changes, and at least one source citation such as `https://www.python.org/downloads/`. The mission journal shows `search_web` and `fetch_url` tool calls. Exit code 0.

## How It Works

The worker agent receives the goal, decides to call `search_web("current stable python version")`, picks a result, then calls `fetch_url` on python.org. Page content is stripped to markdown-ish text and the LLM synthesizes a cited answer. Every step is journaled for replay.

## Troubleshooting

- **No search results**: network issue or blocked search fallback; retry or check connectivity.
- **Empty fetch output**: the target page blocked the request; ask AJA to try another source.
- **Mission hangs**: ensure an LLM provider key is configured; run `aja doctor`.
