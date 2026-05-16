# Custom palette preview (d2 + Source Code Pro + sketch)

Шесть кастомных палеток на той же диаграмме [`session-lifecycle.d2`](session-lifecycle.d2). Все собраны через `vars.d2-config.theme-overrides` (16 цветовых слотов: N1-N7 нейтрали, B1-B6 primary, AA2/AA4/AA5 secondary, AB4/AB5 tertiary). Шрифт — Source Code Pro (variable TTF из `~/Library/Fonts`). Режим — `--sketch`.

Команда:
```bash
d2 --sketch --pad=20 \
   --font-regular "$HOME/Library/Fonts/SourceCodePro[wght].ttf" \
   --font-italic  "$HOME/Library/Fonts/SourceCodePro-Italic[wght].ttf" \
   --font-bold    "$HOME/Library/Fonts/SourceCodePro[wght].ttf" \
   pN.d2 pN.svg
```

---

<table>
<tr>
  <td align="center"><b>P1 — Brand Dark</b><br><sub>navy/orange/cream, warm dark</sub><br><img src="palette-preview/p1-brand-dark.svg" width="280"></td>
  <td align="center"><b>P2 — Brand Light</b><br><sub>cream bg, navy text, orange accent</sub><br><img src="palette-preview/p2-brand-light.svg" width="280"></td>
</tr>
<tr>
  <td align="center"><b>P3 — Dracula</b><br><sub>#282a36 / mauve / pink / cyan</sub><br><img src="palette-preview/p3-dracula.svg" width="280"></td>
  <td align="center"><b>P4 — Tokyo Night</b><br><sub>#1a1b26 / blue / purple</sub><br><img src="palette-preview/p4-tokyo-night.svg" width="280"></td>
</tr>
<tr>
  <td align="center"><b>P5 — Nord</b><br><sub>#2e3440 / frost / aurora</sub><br><img src="palette-preview/p5-nord.svg" width="280"></td>
  <td align="center"><b>P6 — Gruvbox Dark</b><br><sub>#282828 / orange / green</sub><br><img src="palette-preview/p6-gruvbox.svg" width="280"></td>
</tr>
</table>

---

## Что такое слоты

| Slot | Назначение | Где видно |
|---|---|---|
| N7 → N5 | Backgrounds (dark → mid) | Холст диаграммы |
| N4 → N3 | Borders / dividers | Контуры |
| N2 → N1 | Foreground text | Подписи |
| B1 → B6 | Primary accent (light → dark) | Большинство shape'ов |
| AA2, AA4, AA5 | Secondary accent | Highlighted nodes |
| AB4, AB5 | Tertiary accent | Edges / hints |

Логика наполнения brand-dark: N7 ≈ `#1c1810` (тёмный jam), B1 = `#e8632b` (brand orange), N1-N2 — cream `#faf2e6 / #e8d8b8`. То есть «обложка книги логотипа» = primary, «страницы книги» = neutrals, «шляпа» уходит как dark surface.

## Как создать свою

1. Берёшь любую палетку (например, [coolors.co](https://coolors.co/) или из брендбука) — нужны 5-7 нейтралей (от тёмного к светлому) и 1-3 акцента.
2. Раскладываешь по слотам по таблице выше.
3. Кладёшь в `vars.d2-config.theme-overrides` любого `.d2`-файла.
4. Рендеришь.

`theme-id` (число рядом) — стартовая база; overrides переписывают то, что задал, остальное берётся из неё. Удобно стартовать с близкой по тону темы (200 для тёмных, 0 для светлых).
