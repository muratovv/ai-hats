# scripts/demo.tape — hero demo for ai-hats README.
# Story: "Do. Reflect. Repeat." — config status → session list → active hypotheses.
#
# Run from the repo root with `ai-hats` already in PATH (e.g. activated venv):
#   source .venv/bin/activate && vhs scripts/demo.tape
#
# Outputs land in docs/assets/. See docs/assets/README.md for full regeneration
# notes (logo, social card, demo).
#
# Config kept minimal — vhs's defaults (JetBrainsMono Nerd Font Mono) are
# already excellent. We override only what we need for the story to fit.

Output docs/assets/demo.gif
Output docs/assets/demo.mp4

Set FontSize 22
Set Width 1400
Set Height 850
Set Theme "Dracula"
Set Framerate 24

Hide
Type "clear" Enter
Sleep 300ms
Show

# Do — the assembled role
Type "ai-hats config status"
Sleep 400ms
Enter
Sleep 3.5s

# Reflect — last few real sessions
Type "ai-hats session list --productive --last 3"
Sleep 400ms
Enter
Sleep 3.5s

# Repeat — hypotheses the framework formed about its own behaviour
Type "ai-hats task hyp list --status active"
Sleep 400ms
Enter
Sleep 4s
