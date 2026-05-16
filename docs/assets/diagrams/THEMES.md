# d2 theme & font preview

The same [`session-lifecycle.d2`](session-lifecycle.d2) source rendered in every built-in d2 theme, in two modes: **sketch** (hand-drawn) and **flat** (standard rendering). Used during HATS-348 to pick a base before custom palettes.

Command: `d2 [--sketch] --theme=<id> in.d2 out.svg`

> Final pick for HATS-348 was a custom brand-light palette (see [`PALETTES.md`](PALETTES.md)) on top of `theme-id: 0`. The gallery below stays as a reference for future diagrams that want a different starting point.

---

## Light themes

<table>
<tr><th>Theme</th><th>Sketch</th><th>Flat</th></tr>
<tr><td><b>0 — Neutral Default</b></td><td><img src="themes-preview/t0-sketch.svg" width="280"></td><td><img src="themes-preview/t0-flat.svg" width="280"></td></tr>
<tr><td><b>1 — Neutral Grey</b></td><td><img src="themes-preview/t1-sketch.svg" width="280"></td><td><img src="themes-preview/t1-flat.svg" width="280"></td></tr>
<tr><td><b>3 — Flagship Terrastruct</b></td><td><img src="themes-preview/t3-sketch.svg" width="280"></td><td><img src="themes-preview/t3-flat.svg" width="280"></td></tr>
<tr><td><b>4 — Cool Classics</b></td><td><img src="themes-preview/t4-sketch.svg" width="280"></td><td><img src="themes-preview/t4-flat.svg" width="280"></td></tr>
<tr><td><b>5 — Mixed Berry Blue</b></td><td><img src="themes-preview/t5-sketch.svg" width="280"></td><td><img src="themes-preview/t5-flat.svg" width="280"></td></tr>
<tr><td><b>6 — Grape Soda</b></td><td><img src="themes-preview/t6-sketch.svg" width="280"></td><td><img src="themes-preview/t6-flat.svg" width="280"></td></tr>
<tr><td><b>7 — Aubergine</b></td><td><img src="themes-preview/t7-sketch.svg" width="280"></td><td><img src="themes-preview/t7-flat.svg" width="280"></td></tr>
<tr><td><b>8 — Colorblind Clear</b></td><td><img src="themes-preview/t8-sketch.svg" width="280"></td><td><img src="themes-preview/t8-flat.svg" width="280"></td></tr>
<tr><td><b>100 — Vanilla Nitro Cola</b></td><td><img src="themes-preview/t100-sketch.svg" width="280"></td><td><img src="themes-preview/t100-flat.svg" width="280"></td></tr>
<tr><td><b>101 — Orange Creamsicle</b></td><td><img src="themes-preview/t101-sketch.svg" width="280"></td><td><img src="themes-preview/t101-flat.svg" width="280"></td></tr>
<tr><td><b>102 — Shirley Temple</b></td><td><img src="themes-preview/t102-sketch.svg" width="280"></td><td><img src="themes-preview/t102-flat.svg" width="280"></td></tr>
<tr><td><b>103 — Earth Tones</b></td><td><img src="themes-preview/t103-sketch.svg" width="280"></td><td><img src="themes-preview/t103-flat.svg" width="280"></td></tr>
<tr><td><b>104 — Everglade Green</b></td><td><img src="themes-preview/t104-sketch.svg" width="280"></td><td><img src="themes-preview/t104-flat.svg" width="280"></td></tr>
<tr><td><b>105 — Buttered Toast</b></td><td><img src="themes-preview/t105-sketch.svg" width="280"></td><td><img src="themes-preview/t105-flat.svg" width="280"></td></tr>
<tr><td><b>300 — Terminal</b></td><td><img src="themes-preview/t300-sketch.svg" width="280"></td><td><img src="themes-preview/t300-flat.svg" width="280"></td></tr>
<tr><td><b>301 — Terminal Grayscale</b></td><td><img src="themes-preview/t301-sketch.svg" width="280"></td><td><img src="themes-preview/t301-flat.svg" width="280"></td></tr>
<tr><td><b>302 — Origami</b></td><td><img src="themes-preview/t302-sketch.svg" width="280"></td><td><img src="themes-preview/t302-flat.svg" width="280"></td></tr>
<tr><td><b>303 — C4</b></td><td><img src="themes-preview/t303-sketch.svg" width="280"></td><td><img src="themes-preview/t303-flat.svg" width="280"></td></tr>
</table>

## Dark themes

<table>
<tr><th>Theme</th><th>Sketch</th><th>Flat</th></tr>
<tr><td><b>200 — Dark Mauve</b></td><td><img src="themes-preview/t200-sketch.svg" width="280"></td><td><img src="themes-preview/t200-flat.svg" width="280"></td></tr>
<tr><td><b>201 — Dark Flagship Terrastruct</b></td><td><img src="themes-preview/t201-sketch.svg" width="280"></td><td><img src="themes-preview/t201-flat.svg" width="280"></td></tr>
</table>

---

## Fonts

d2 has no named "font themes" like Dracula — what you get is either the built-in sketch font (hand-drawn, with `--sketch`) or a system sans-serif (without the flag). Customization is via TTF files:

```bash
d2 --font-regular MyFont-Regular.ttf \
   --font-bold    MyFont-Bold.ttf \
   --font-italic  MyFont-Italic.ttf \
   --sketch --theme=200 \
   in.d2 out.svg
```

Full flag list: `d2 --help | grep font`. The font can be embedded into the SVG (default) or linked externally.

## Online preview

- **Playground (live editing):** https://play.d2lang.com/
- **Theme guide:** https://d2lang.com/tour/themes
- **Sketch mode:** https://d2lang.com/tour/sketch
- **Custom fonts:** https://d2lang.com/tour/fonts
