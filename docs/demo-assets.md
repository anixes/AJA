# Demo Assets Guide

How to record terminal demos (GIFs/screenshots) for the README and docs.

---

## 1. Recording Terminal Sessions

### Option A: asciinema + agg

1. Record a cast file:
   ```bash
   asciinema rec docs/assets/demo.cast
   ```
2. Convert to GIF with [agg](https://github.com/asciinema/agg):
   ```bash
   agg docs/assets/demo.cast docs/assets/demo.gif --speed 1.5 --font-size 14
   ```

### Option B: vhs (charmbracelet, scripted/reproducible)

Write a `.tape` script so demos are regenerable:

```tape
# demo.tape
Output docs/assets/demo.gif
Set FontSize 16
Set Width 1100
Set Height 640
Type "python -m aja run \"find current stable Python version\""
Enter
Sleep 8s
```

Render:
```bash
vhs demo.tape
```

## 2. Recommended Demo Scenarios & Commands

| Scenario | Command | Notes |
|----------|---------|-------|
| First chat interaction | `python -m aja chat` | Show intent parsing + direct execution |
| Web research mission | `python -m aja run "find current stable Python version" --dry-run` | Live search_web → fetch_url → cited synthesis |
| Daily briefing delivery | `python -m aja briefing enable --at "0 7 * * *"` then trigger manually via Telegram | Shows structured markdown briefing |
| TUI dashboard tour | `python -m aja tui --dry-run` | Cycle themes with `s` hotkey; show HTN plan tree |

Keep recordings under 30s and trim idle time (`--speed` in agg or `Sleep` tuning in vhs).

## 3. Output Locations & README Embedding

Place final assets in `docs/assets/`:

```
docs/assets/
  demo-chat.gif
  demo-research.gif
  demo-briefing.gif
  demo-tui.gif
```

Embed in `README.md`:

```markdown
![AJA web research mission](docs/assets/demo-research.gif)
```

Commit both the `.gif` and the source `.cast`/`.tape` files so demos stay reproducible.
