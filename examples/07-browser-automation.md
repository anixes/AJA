# Example 07: Browser Automation Patterns

**Capability**: Playwright-driven browsing — navigate, wait, extract
**Difficulty**: Advanced
**Prerequisites**: Playwright installed (`pip install playwright && playwright install chromium`); a configured LLM provider

## Objective

Drive a real browser session through AJA to scrape content from JavaScript-heavy pages that plain `fetch_url` cannot render.

## Steps

```bash
aja run "Go to https://news.ycombinator.com, wait for the story list to load, extract the top 5 headlines with their links as markdown."
```

Interactive session (browser state persists across turns within a mission):

```bash
aja chat
```
```
Open https://github.com/trending and list the top trending repos.
Now click into the first one and summarize its README.
```

The worker will issue actions equivalent to:
- `browser.navigate` — load the page
- `browser.wait_for_selector` — block until content renders (parameterized timeout)
- `browser.extract_markdown` — pull structured text/links

## Expected Output

Extracted markdown containing headlines/repo names with URLs. Screenshots can be requested for visual verification; browser errors come back as structured kinds (`timeout`, `selector_not_found`, `navigation_failed`) with hints.

## How It Works

Playwright runs as AJA's async browser backend with sessions persisted per mission. Actions are journaled through ActivityRuntime (replay-safe) and locked per-session so parallel activities cannot collide on the same tab. Errors are normalized so the LLM can self-correct (e.g. retry with a longer wait).

## Troubleshooting

- **`playwright not installed`**: run `pip install playwright && playwright install chromium`.
- **selector_not_found**: page may be slower than the default timeout — ask AJA to "wait longer" or use `wait_for_network_idle` first.
- **Bot-blocked sites**: many sites challenge headless browsers; prefer pages that tolerate automation or use `fetch_url` where rendering isn't needed.
