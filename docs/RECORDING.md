# Recording the README demo

The README shows a demo image at [`docs/assets/demo.svg`](assets/demo.svg). That
SVG is a **static poster** generated from the real offline demo — it renders
everywhere (including GitHub) and never goes stale. This page covers two things:

1. Regenerating that static SVG.
2. Recording an **animated GIF** to replace it (optional, looks great at the top
   of the README).

## 1. Regenerate the static SVG (no recording, no auth)

```bash
python scripts/make_demo_svg.py     # writes docs/assets/demo.svg
```

It runs example 1 through PromptPilot's real pipeline and lays out a terminal
"poster" as plain SVG (no `<style>`/`<script>` — GitHub strips those from
`<img>`-embedded SVG). Re-run it whenever the demo's extraction changes.

## 2. Record an animated GIF (optional)

GitHub's markdown sanitizer does **not** animate `<img>`-referenced SVG, so the
reliable animated format is **GIF**. The staging script
[`scripts/demo_cast.sh`](../scripts/demo_cast.sh) runs the exact command
sequence to record — it drives the zero-setup offline demo, so no API key or
coding-agent auth is needed.

### macOS / Linux — asciinema + agg

```bash
# 1. install the tools (once)
brew install asciinema agg          # macOS; on Linux use pipx/apt/cargo
# pipx install asciinema && cargo install --git https://github.com/asciinema/agg

# 2. record the staged sequence
asciinema rec demo.cast --overwrite --command "bash scripts/demo_cast.sh"

# 3. convert to a GIF and drop it next to the SVG
agg --font-size 22 --theme asciinema demo.cast docs/assets/demo.gif
```

Tune pacing with env vars before recording, e.g. `READ_PAUSE=3.5 TYPE_PAUSE=1.5`.

### Want the live coding agent in the recording?

Uncomment the `prpt --dry-run ...` line (or add a real `prpt "..."` call) in
[`scripts/demo_cast.sh`](../scripts/demo_cast.sh). A real `prpt` run uses **your**
auth and spends tokens, so do that take yourself rather than in CI.

### Wire the GIF into the README

Once `docs/assets/demo.gif` exists, point the README at it — change the one image
line under `## Demo`:

```diff
- ![PromptPilot demo](docs/assets/demo.svg)
+ ![PromptPilot demo](docs/assets/demo.gif)
```

Keep `demo.svg` as the committed fallback. Recommended GIF budget: < 2 MB and
< ~20 s so it loads fast and loops cleanly.

### Windows note

`asciinema`/`agg` are easiest on macOS/Linux (or WSL). On native Windows, record
the same `scripts/demo_cast.sh` sequence with a terminal recorder such as
[Terminalizer](https://github.com/faressoft/terminalizer) (`npm i -g
terminalizer`) and export to GIF, or just run it under WSL.
