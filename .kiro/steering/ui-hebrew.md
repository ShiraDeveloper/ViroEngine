---
inclusion: fileMatch
fileMatchPattern: 'frontend/**'
---

# Safe Line — Hebrew UI and RTL Rules

The product language is Hebrew, right to left. The user is a laboratory technician, not a
developer.

## 1. All copy in one place

Every user-visible string lives in `frontend/src/copy/he.ts`. **No literal Hebrew in a component.**

```tsx
// wrong
<button>אישור</button>
// right
<button>{review.confirm}</button>
```

Functions for interpolation, so word order stays correct:
```ts
progress: (done: number, total: number) => `${done} מתוך ${total} הושלמו`,
```

## 2. Direction

- `<html dir="rtl" lang="he">` in `index.html`.
- **Logical properties only:** `ms-*`, `me-*`, `ps-*`, `pe-*`, `start-*`, `end-*`,
  `text-start`, `text-end`. Never `ml-*`, `mr-*`, `left-*`, `right-*`, `text-left`.
- Tables read right to left. The Confidence Ribbon sits on the row's **inline-start** (the right
  edge in RTL).
- Directional icons mirror with `rtl:-scale-x-100` (chevrons, arrows). **Status icons are never
  mirrored.**
- `lang="en"` on Latin fragments so a screen reader switches voice.

## 3. Numbers must be isolated

Hebrew is RTL, digits are LTR. Without isolation, `18/06/2026` renders with its segments reordered
and `X132767` loses its leading letter.

```tsx
import { ltr } from '@/lib/rtl';
<span>{ltr('18/06/2026')}</span>     // dir="ltr" + font-mono
<span>{ltr('011223344')}</span>
<span>{ltr('X132767')}</span>
```

Applies to: dates, national IDs, central numbers, commitment numbers, page counts, durations,
percentages, file sizes. Always `font-mono` with `tabular-nums` so digits line up for comparison
against a crop.

## 4. Tone

Short, calm, professional. The technician is doing this all day.

- Direct and informative: `3 שדות לבדיקה`, not `אופס! נראה שיש כמה בעיות`.
- Second person feminine singular (the lab technician): `בדקי`, `בחרי`, `הזיני`, `לחצי`.
- No exclamation marks. No emoji in copy — status icons are separate components.
- Errors say what happened **and** what the limit is:
  `הקובץ גדול מדי. המגבלה היא 50MB.` not `שגיאה בהעלאה`.
- Empty states explain rather than apologize: `כל העמודים שויכו לטפסים.`

## 5. Approved status wording

These exact strings. Do not paraphrase — they appear in the UI, in Excel comments and in the
metrics, and must match.

| Level | Text | Icon | Colour token |
|---|---|---|---|
| A | `אומת בברקוד` | `✓` | `state.verified` |
| B | `שני קוראים הסכימו` | `✓` | `state.verified` |
| C | `נקרא פעם אחת` | `◐` | `state.single` |
| D | `לא בטוח – בדקי` | `⚠` | `state.review` |

Level-D reasons come from the closed set in `docs/PLAN.md` §5.2. Never invent a reason string.

Other fixed wording: `נשלח חיתוך בלבד` (a cloud step in the Reading Trail) ·
`לא ברשימה` (unknown sender) · `לא נמצא. עדיף ריק מסומן מערך מנוחש.` (empty field note) ·
`נסגרו על המחשב` (local close counter).

## 6. Never colour alone

Every confidence state renders **icon + Hebrew text + colour**. A cell distinguished only by
background colour is a review rejection — it fails accessibility and it reads badly on a projector.

## 7. Typography

- `font-sans` → Heebo, for all Hebrew text. Line height 1.5 for prose; Hebrew needs more leading
  than Latin at the same size.
- `font-mono` → JetBrains Mono, for every number.
- Sizes from `tokens.fontSize`. Never a literal `px` font size.

## 8. Accessibility

- Everything reachable and operable by keyboard. The review screen is keyboard-first.
- `boxShadow.focus` on `:focus-visible`. Never `outline: none`.
- Real `<table>` semantics with `scope`; cells carry `aria-describedby` pointing at the reason text.
- Live regions: SSE updates announce through `aria-live="polite"`.
- Motion wrapped in `prefers-reduced-motion`; durations collapse to `0ms`.
- Contrast at least 4.5:1 for text, 3:1 for borders. The tokens already satisfy this — do not
  introduce new colour pairs without checking.
- `axe-core` assertions exist for all six screens and must stay green.

## 9. Keyboard shortcuts (review screen)

Fixed. Do not change without updating `review.shortcuts` in `he.ts`.

| Key | Action |
|---|---|
| `Tab` / `Shift+Tab` | Next / previous **flagged** field only, scrolled into view |
| `Enter` | Accept the highlighted candidate |
| `1`–`3` | Pick candidate 1–3 directly |
| `E` | Manual typing |
| `Ctrl+Z` | Undo the last edit, via the API |
| `/` | Focus the filter |
| `?` | Shortcut sheet |
| `Esc` | Close the Evidence Lens or a dialog |

Target: three flagged fields resolved in under 60 seconds using the keyboard alone.
