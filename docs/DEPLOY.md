# העלאה לענן — מדריך צעד אחר צעד

**ענן נבחר: Hugging Face Spaces (Docker), חינם.** 2 מעבדים ו-16GB זיכרון בחינם, תמיכה ב-Docker,
כתובת ציבורית עם HTTPS. אותו קונטיינר מגיש גם את האתר (`/`) וגם את ה-API (`/v1`), כך שיש כתובת אחת
ומקום אחד להגדרות. (Render החינמי נותן 0.1 מעבד ו-512MB, איטי מדי. Vercel מתאים רק לאתר, בלי השרת.)

---

## שלב 1 — מפתחות Gemini (חובה)

1. נכנסים ל-https://aistudio.google.com/apikey עם חשבון Google.
2. לוחצים **Create API key**, ומעתיקים את המפתח (מתחיל ב-`AIza`).
3. **חשוב — מכסה:** בחינם יש כ-**20 בקשות ביום לכל מודל לכל פרויקט**. 30 טפסים צורכים כ-40 בקשות.
   בודקים את הניצול ב-https://aistudio.google.com/usage (או https://ai.dev/rate-limit).
   - **הדרך המומלצת לגמר:** מפעילים חיוב (Billing) על הפרויקט: ב-AI Studio, לוחצים על **Set up billing** ליד המפתח.
     חשבון Google Cloud חדש מקבל **קרדיט ניסיון של $300** (https://cloud.google.com/free), כך שבפועל
     זה לא עולה כסף. עמוד עולה בערך $0.002. בנוסף, במפתח בתשלום Google **לא** משתמשת בקלט לאימון, וזה יתרון לפרטיות.
     מומלץ להגדיר תקציב (Budget alert) של $5.
   - חלופה בלי חיוב: כל חברת צוות פותחת מפתח **מהחשבון האישי שלה**, ומכניסים את כולם מופרדים בפסיק.
     לא לפתוח חשבונות או פרויקטים נוספים רק כדי "לאפס" מכסה, כי זה מנוגד לתנאי השימוש.
4. המכסה מתאפסת פעם ביום (חצות שעון קליפורניה, 10:00 בבוקר בישראל). **לא להריץ ניסויים כבדים ביום הגמר.**

אופציונלי, כגיבוי: מפתח OpenRouter מ-https://openrouter.ai/keys, יחד עם מזהה מודל ראייה חינמי
(`OPENROUTER_VISION_MODEL`, לבחור ב-https://openrouter.ai/models?input_modalities=image&max_price=0).

## שלב 2 — חשבון Hugging Face וטוקן

1. נרשמים ב-https://huggingface.co/join.
2. יוצרים טוקן **Write** ב-https://huggingface.co/settings/tokens (New token → Type: Write). הטוקן מתחיל ב-`hf_`.

## שלב 3 — יצירת ה-Space

1. נכנסים ל-https://huggingface.co/new-space.
2. ממלאים: **Space name**, למשל `safe-line`. **SDK:** `Docker` → `Blank`. **Hardware:** `CPU basic · FREE`.
   **Visibility:** `Public` (כדי שהשופטים יוכלו להיכנס; הכניסה מוגנת בקוד גישה).
3. לוחצים **Create Space**.

## שלב 4 — סודות והגדרות (לפני ההעלאה)

ב-Space: **Settings → Variables and secrets**.

| שם | סוג | ערך |
|---|---|---|
| `GEMINI_API_KEYS` | **Secret** | המפתחות מופרדים בפסיק: `AIza...,AIza...` |
| `APP_ACCESS_CODE` | **Secret** | קוד הכניסה שתתנו לשופטים, למשל `virology2026` |
| `ALLOWED_ORIGINS` | Variable | `https://<user>-safe-line.hf.space` |
| `FORWARDED_ALLOW_IPS` | Variable | `*` |
| `JOB_TTL_MINUTES` | Variable | `30` |

**לא** מגדירים `API_KEY` בהתקנה הזו: האתר והשרת יושבים באותה כתובת, וההעלאה מוגנת בקוד הגישה.

## שלב 5 — העלאה

ב-PowerShell, מתיקיית הפרויקט:

```powershell
git add -A
git commit -m "Vision-first engine, redesign, one-container deploy"
$env:HF_TOKEN = "hf_..."                         # הטוקן משלב 2
./scripts/deploy_hf.ps1 -Space "<user>/safe-line"
```

הסקריפט מעלה **רק** את מה ששמור ב-git. הקבצים `.env` ו-`reference/` (קובצי הדוגמה) לעולם לא עולים.
בניית התמונה לוקחת 5 עד 10 דקות. רואים את ההתקדמות בלשונית **Logs → Build** של ה-Space.

אם PowerShell חוסם הרצת סקריפטים: `Set-ExecutionPolicy -Scope Process Bypass`.

## שלב 6 — בדיקה

1. `https://<user>-safe-line.hf.space/v1/health` צריך להחזיר `"status":"ok"`.
2. פותחים את `https://<user>-safe-line.hf.space`, מקלידים את קוד הגישה ומעלים טופס דוגמה אחד.
3. נכנסים ל-**Logs → Container** ומוודאים שאין הודעות `providers_exhausted` (שמשמעותן שהמכסה נגמרה).

## לפני ההצגה

- Space חינמי "נרדם" אחרי כ-48 שעות בלי שימוש. **חצי שעה לפני ההצגה** פותחים את האתר ומריצים טופס אחד כחימום.
- בודקים שנשארה מכסה (שלב 1.3).
- אחרי ההאקתון: מוחקים את המפתחות ב-AI Studio ואת הטוקן ב-Hugging Face.

## תקלות נפוצות

| סימפטום | סיבה | פתרון |
|---|---|---|
| כל השדות כתומים, וב-Logs מופיע `PROVIDER_DAILY_QUOTA` | המכסה היומית נגמרה | להוסיף מפתח, או להפעיל חיוב (שלב 1) |
| `Build error` ב-`npm ci` | `package-lock.json` לא עודכן | להריץ `npm install` בתיקייה `frontend`, ואז commit והעלאה מחדש |
| המסך נטען אבל ההעלאה נכשלת עם 401 | קוד גישה שגוי | לבדוק את `APP_ACCESS_CODE` |
| בכתובת `huggingface.co/spaces/...` רואים מסגרת ריקה | חלק מהדפדפנים חוסמים iframe | להיכנס ישירות ל-`https://<user>-safe-line.hf.space` |

## הרצה מקומית (Windows)

```powershell
pip install -r requirements.txt
cd frontend; npm ci; $env:VITE_API_BASE="/v1"; npx vite build; cd ..
python -m uvicorn backend.app:app --port 8000      # http://localhost:8000
```
