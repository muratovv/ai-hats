# docs/assets/ — visual assets

Logo, demo recording, and GitHub social card for the project README.

## Inventory

| File | Purpose | Source |
|---|---|---|
| `logo.png` | Transparent original 1024×1024 — main logo asset | Gemini image gen (Imagen 4), see prompt below |
| `logo-256.png` | README hero, inline use | Resized from `logo.png` |
| `logo-128.png` | favicon, badges | Resized from `logo.png` |
| `logo-64.png` | Inline icon in body text | Resized from `logo.png` |
| `logo-silhouette.svg` | Vector silhouette (single-color, scalable) | `potrace` trace of `logo.png` alpha mask |
| `social-card.png` | GitHub social preview 1280×640 | Composited from `logo.png` + wordmark + tagline |
| `demo.gif` | Hero demo animation for README | `vhs scripts/demo.tape` |
| `demo.mp4` | Higher-quality demo (for landing or docs) | Same as above (vhs dual output) |

## How to regenerate the logo from scratch

The logo was generated in [Gemini](https://gemini.google.com) (Imagen 4) with
the prompt below. If you want to iterate on the visual:

```
Minimalist vector logo: a stack of three slightly different hats
(detective fedora on top, hard hat in middle, wizard cap at bottom),
arranged in a precise geometric stack with a subtle 3D depth effect.
Each hat in a single accent color (#FF6B35 orange, #6B5B95 purple,
#88B04B green). Flat design, thick outline strokes, no gradients,
no shadows. Centered on a solid white background. The visual should
read clearly even at 32x32 pixels. Style: Lucide/Phosphor icon
aesthetic, suitable for a developer-tool logo.
```

(The current `logo.png` is a variant of this concept: a fedora atop
a stack of two books, navy + orange + white palette.)

After Gemini produces the raster, drop the result at `docs/assets/logo-raw.png`
and run the post-processing below.

## Post-processing pipeline

All steps use [ImageMagick](https://imagemagick.org) (`brew install imagemagick`)
and [potrace](http://potrace.sourceforge.net) (`brew install potrace`).

### 1. Strip white background → transparent PNG

```bash
magick docs/assets/logo-raw.png \
    -fuzz 8% -transparent white \
    -trim +repage \
    docs/assets/logo.png
```

`-fuzz 8%` tolerates near-white pixels around outline anti-aliasing.
`-trim +repage` crops empty borders and resets the coordinate frame.

### 2. Size variants (256 / 128 / 64 px square, centered)

```bash
for size in 256 128 64; do
    magick docs/assets/logo.png \
        -resize ${size}x${size} \
        -background none -gravity center -extent ${size}x${size} \
        docs/assets/logo-${size}.png
done
```

### 3. Vector silhouette (SVG)

`potrace` only operates on binary bitmaps, so we trace the alpha mask.
The result is a single-color silhouette useful as a scalable favicon
or for monochrome contexts. The colored logo stays as PNG.

```bash
magick docs/assets/logo.png \
    -alpha extract -threshold 50% -negate \
    /tmp/logo-bw.pbm
potrace /tmp/logo-bw.pbm -s -o docs/assets/logo-silhouette.svg
rm /tmp/logo-bw.pbm
```

### 4. GitHub social preview card (1280 × 640)

Composes the logo on a Dracula-themed background with the wordmark
and tagline. Uses [JetBrains Mono](https://www.jetbrains.com/lp/mono/)
(`brew install --cask font-jetbrains-mono`).

```bash
JBM="$HOME/Library/Fonts/JetBrainsMono[wght].ttf"

magick -size 1280x640 xc:'#282A36' \
    \( docs/assets/logo.png -resize 360x360 \) \
    -gravity west -geometry +120+0 -compose over -composite \
    -font "$JBM" -weight 700 -pointsize 96 -fill '#F8F8F2' \
    -gravity east -annotate +160+-45 'ai-hats' \
    -font "$JBM" -weight 400 -pointsize 32 -fill '#FF6B35' \
    -gravity east -annotate +160+45 'Do. Reflect. Repeat.' \
    docs/assets/social-card.png
```

Palette:
- `#282A36` — Dracula background
- `#F8F8F2` — Dracula foreground (wordmark)
- `#FF6B35` — accent orange (tagline, matches logo accent)

### 5. Upload the social card to GitHub

There is **no public API** for setting the repository social preview
image — only the web UI works
([GitHub Community Discussion #52294](https://github.com/orgs/community/discussions/52294)).

1. Open `https://github.com/<owner>/ai-hats/settings`
2. Scroll to **Social preview**
3. Click **Edit** → upload `docs/assets/social-card.png`

GitHub caches social previews aggressively — allow up to a few minutes
before the new image shows in Open Graph previews.

## How to regenerate the demo GIF

The demo records three real `ai-hats` commands telling the
"Do. Reflect. Repeat." story on the actual project state.

Requirements:
- `vhs` ≥ 0.11 (`brew install vhs`)
- `ai-hats` available in `$PATH` (activate the project venv first)

```bash
source .venv/bin/activate    # so `ai-hats` resolves
vhs scripts/demo.tape
```

This regenerates both `docs/assets/demo.gif` and `docs/assets/demo.mp4`
from `scripts/demo.tape`.

### Common pitfalls

- **Blurry glyphs / huge spacing between letters**
  Means the FontFamily in `.tape` did not match an installed font name
  and headless Chrome fell back to a system monospace.
  Fix: keep the default (no `Set FontFamily` line) — vhs ships
  JetBrainsMono Nerd Font Mono as the embedded default and it renders
  cleanly. If overriding, use the exact name from `fc-list` output,
  e.g. `"JetBrainsMono Nerd Font Mono"`.
- **`ffmpeg: libx265.NNN.dylib not found`**
  Homebrew bumped x265 minor version and ffmpeg has stale linkage.
  Fix: `brew reinstall ffmpeg`.
- **GIF over ~1 MB** for README — compress with `gifsicle -O3 --colors 128`
  or shorten the scenes in `scripts/demo.tape`.

## Notes on choice of asset format

- `logo.png` is the canonical asset. Size variants are derived; do not
  hand-edit them. If you redesign the logo, regenerate the variants.
- `logo-silhouette.svg` is a derivative (single-color trace), not the
  master vector. The original logo is raster (Gemini output). If a true
  vector master is ever produced, replace `logo.png` and re-derive.
- `demo.gif` is what GitHub embeds inline in the README; `demo.mp4` is
  kept for any future landing page where higher quality matters.
