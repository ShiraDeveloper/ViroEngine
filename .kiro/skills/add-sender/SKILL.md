---
name: add-sender
description: Add or correct a sending institution in the Safe Line closed list (config/senders.json) - aliases, commitment label synonyms, deny lists and pattern hints. Use when a form arrives from an institution reported as "לא ברשימה", when a commitment number is extracted from the wrong label, or when regenerating the list from the public source.
---

# Add a sending institution

The engine selects the sending institution from a **closed list** and never invents one. Adding an
institution is a configuration change, not a code change — the laboratory can do this without a
developer.

## When to use

- A form is classified `"לא ברשימה"` (not on the list).
- The right institution is identified but the commitment number comes from the wrong label.
- A trap label needs adding to a deny list.
- The public hospital list needs regenerating.

## File and schema

`config/senders.json`. Every entry validates against `SenderSpec`
(`backend/core/models.py`).

```json
{
  "canonical": "וולפסון",
  "family": "hospital",
  "source": "wikipedia",
  "aliases": ["וולפסון", "אדית וולפסון", "wolfson"],
  "keywords": ["וולפסון", "אדית וולפסון", "המרכז הרפואי האוניברסיטאי"],
  "commitment_labels": ["מספר התחייבות", "התחייבות"],
  "commitment_deny": ["מס' קליטה"],
  "pattern_hints": ["^\\d{6,8}$"],
  "barcode_is_commitment": null,
  "notes": "VERIFY: highlighted vertical barcode (7254627) may be the commitment. B12, B13."
}
```

| Field | Purpose |
|---|---|
| `canonical` | The exact name written to the spreadsheet. Pick one and never vary it |
| `family` | `hmo` · `hospital` · `private_lab` · `military` · `state_institute` |
| `source` | `constant` (the four HMOs) · `wikipedia` · `moh` · `seed` |
| `aliases` | Every way the name appears, including English and common misspellings |
| `keywords` | Distinctive **letterhead** words used for fuzzy matching |
| `commitment_labels` | The labels that precede the commitment number at this institution |
| `commitment_deny` | Labels that must never yield a commitment number here |
| `pattern_hints` | Regex **hints only**. They adjust the score; they never reject a value |
| `barcode_is_commitment` | `true` / `false` / `null` when unknown — `null` makes barcode payloads hint-level candidates |
| `notes` | Observations, page labels, open questions |

## Procedure

1. **Find the letterhead text.** Run the classifier on the page to see what the OCR actually read:
   ```bash
   python -m tools.demo classify <PAGE_LABEL> --show-ocr
   ```
   Use words the OCR reliably returns. A word it misreads is useless as a keyword.

2. **Choose the canonical name.** If the institution already exists under another name, add an
   alias instead of a second entry. Duplicate entries split the learning store.

3. **Add `keywords`.** Distinctive and printed. Avoid generic words shared by many institutions
   (`מרכז רפואי`, `בית חולים`) — they cause collisions below the 88 threshold.

4. **Add `commitment_labels`** from the actual form. Observed synonyms across the sample set:
   `מספר התחייבות` · `התחייבות` · `מספר ההתחייבות` · `דרישה` · `דגימה` · `מספר דגימה` ·
   `אסמכתא` · `אסמכתא תקציבית מספר` · `התחייבות לתשלום`.

5. **Add `commitment_deny`** for traps on this form. The global deny list already covers `מקרה`,
   `מס' ביקור`, `מס' קליטה`, `מספר מבחנה`, `פקס`, `טלפון` — add only institution-specific ones.

6. **Add `pattern_hints` only if confident.** Remember:
   - Laniado's commitment contains a letter: `^[A-Z]\d{4,8}$`.
   - Lengths across the sample set range from 4 to 9.
   - **A wrong hint lowers the score of a correct value.** When unsure, omit it. No hint gives a
     neutral 0.5.

7. **If no commitment number exists**, set `"commitment_labels": []` and say so in `notes` — as
   for Rosenblatt. The field will be empty and flagged, which is the correct outcome.

8. **Verify the match:**
   ```bash
   python -c "from backend.adapters.senders.loader import SenderIndex; i=SenderIndex.load(); \
   m=i.match('<letterhead text as OCR read it>'); print(m.canonical if m else 'NO MATCH', m.score if m else '')"
   ```
   A real match scores 92–100. Below 88 returns `None`. If a correct letterhead scores below 88,
   the keywords are wrong — do not lower the threshold.

9. **Add a test** in `backend/tests/unit/test_senders.py`:
   ```python
   def test_matches_wolfson_letterhead():
       assert SenderIndex.load().match("המרכז הרפואי האוניברסיטאי ע\"ש אדית וולפסון").canonical == "וולפסון"
   ```

10. **Run the evaluation** to confirm nothing regressed:
    ```bash
    python -m eval.run_eval --gold eval/gold.csv --split dev
    ```

## Regenerating from the public source

Per the challenge guidelines the hospital list comes from Wikipedia "בתי חולים בישראל" or the
Ministry of Health institutions list on gov.il. The four HMOs are constants.

```bash
python -m tools.build_senders --out config/senders.json --seed config/senders.seed.json
```

**Known blocker.** The team's network (NetFree) blocks Wikipedia — HTTP 418 with a cross-origin
redirect. The tool treats any failure as a signal to fall back to `config/senders.seed.json`, and
the build still succeeds. To get the live list: run the tool from an unfiltered network once, or
request that the page be allowed, or use the gov.il list.

Regeneration **preserves** manual enrichment: aliases, labels, deny lists, hints and notes are
merged by `canonical`. Never hand-edit the generated header fields `source_url` / `fetched_at`.

## Rules

- At runtime the engine **never** touches the internet for this list. It reads the committed file.
- Never add an institution just to make a page pass. If the real institution is unknown, leave it
  flagged.
- `destination_suppress` is not an institution list — it holds the laboratory's own names
  (`תל השומר`, `שיבא`, `מעבדת נגיפים`), which appear on almost every form and must never be
  returned as the sender.
- After three confirmations the learning layer may **propose** a new institution. A proposal needs
  explicit approval before it affects matching; approving it is this same procedure.
