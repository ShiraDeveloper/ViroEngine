/**
 * Every user-visible string (docs/PLAN.md §9.5, .kiro/steering/ui-hebrew.md).
 * Status wording is fixed: it also appears in Excel comments and metrics.
 */

export const common = {
  appName: 'שורה בטוחה',
  skipToContent: 'דלגי לתוכן הראשי',
  loading: 'טוען…',
  retry: 'נסי שוב',
  close: 'סגירה',
  loadFailed: 'הנתונים לא נטענו. בדקי את החיבור ונסי שוב.',
  notAvailable: '—',
  mockBanner: 'מצב הדגמה: הנתונים מגיעים מקובץ מקומי ואינם נשלחים לשרת.',
  statusLabel: 'סטטוס',
} as const;

export const nav = {
  label: 'ניווט ראשי',
  upload: 'העלאה',
  processing: 'עיבוד',
  review: 'בדיקה',
  export: 'ייצוא',
  dashboard: 'מדדים',
  logout: 'יציאה',
} as const;

export const login = {
  title: 'שורה בטוחה',
  subtitle: 'מטפסי התחייבות לשורה מאומתת',
  codeLabel: 'קוד גישה',
  codePlaceholder: 'הזיני את קוד הגישה',
  submit: 'כניסה',
  demoBanner: 'מערכת הדגמה. העלו טפסי דוגמה בלבד, לא מסמכים אמיתיים.',
  demoBannerLocalOnly: 'קובץ שאינו מטפסי הדוגמה יעובד במחשב בלבד, בלי שליחה לענן.',
  errorWrongCode: 'קוד הגישה שגוי. נסי שוב.',
  errorNoCode: 'נדרש קוד גישה.',
} as const;

export const upload = {
  title: 'העלאת טפסים',
  single: 'טופס בודד',
  batch: 'ערימה',
  modeLabel: 'מצב העלאה',
  dropTitle: 'גררי לכאן טפסים',
  dropHint: 'PDF · JPG · PNG · ZIP · עד 50MB',
  browse: 'או בחרי קובץ',
  rejectedType: 'סוג הקובץ אינו נתמך. אפשר PDF, JPG, PNG או ZIP.',
  tooLarge: 'הקובץ גדול מדי. המגבלה היא 50MB.',
  tooManyPages: 'יותר מדי עמודים. המגבלה היא 200 עמודים.',
  uploading: 'מעלה…',
  start: 'התחילי עיבוד',
  privacyNote: 'העיבוד מתחיל במחשב. רק שדות שלא הצלחנו לקרוא נשלחים כחיתוך קטן.',
  demoJob: 'פתיחת עבודת הדגמה',
} as const;

export const processing = {
  title: 'מעבדת את הטפסים',
  progress: (done: number, total: number) => `${done} מתוך ${total} הושלמו`,
  kpiLocal: 'נסגרו על המחשב',
  kpiCrops: 'חיתוכים שנשלחו',
  kpiFlagged: 'ממתינים לבדיקה',
  stageNames: {
    S0: 'קליטה',
    S1: 'מדידה',
    S2: 'תיקון',
    S3: 'קריאה מקומית',
    S4: 'איתור אזורים',
    S5: 'זיהוי גורם',
    S6: 'מועמדים',
    S7: 'קריאה שנייה',
    S8: 'ציון',
    S9: 'השלמה בענן',
    S10: 'בניית שורות',
  },
  cancel: 'ביטול',
  failedPage: 'עמוד שלא עובד',
  goToReview: 'למסך הבדיקה',
  pagesTitle: 'עמודים',
  pageQueued: 'ממתין',
  pageRunning: 'בעיבוד',
  pageDone: 'הסתיים',
  waiting: 'ממתינה לעדכון מהשרת.',
} as const;

export const review = {
  title: 'בדיקה ואישור',
  columns: {
    sender: 'גורם שולח',
    firstName: 'שם פרטי',
    lastName: 'שם משפחה',
    nationalId: 'ת"ז',
    central: 'מספר מרכזי',
    receiptDate: 'תאריך קבלה',
    commitment: 'מספר התחייבות',
  },
  levels: {
    A: 'אומת בברקוד',
    B: 'שני קוראים הסכימו',
    C: 'נקרא פעם אחת',
    D: 'לא בטוח – בדקי',
  },
  rowRibbon: (flagged: number) => (flagged === 0 ? 'אין שדות לבדיקה' : `${flagged} שדות לבדיקה`),
  tableCaption: 'שורות התחייבות שחולצו מהטפסים',
  rowHeader: 'שורה',
  filterAll: 'הצג הכול',
  filterReview: 'רק לבדיקה',
  filterLabel: 'סינון שורות',
  evidenceTitle: 'ראיה',
  evidenceHint: 'בחרי שדה בטבלה כדי לראות מהיכן נקרא.',
  trailTitle: 'מי קרא מה',
  cloudChip: 'נשלח חיתוך בלבד',
  candidatesTitle: 'בחרי ערך',
  manualEntry: 'הקלדה',
  emptyFieldNote: 'לא נמצא. עדיף ריק מסומן מערך מנוחש.',
  orphansTab: 'עמודים יתומים',
  orphansEmpty: 'כל העמודים שויכו לטפסים.',
  orphansTitle: 'עמודים שלא שויכו לטופס',
  rowsTab: 'שורות',
  confirm: 'אישור',
  undo: 'ביטול שינוי',
  shortcuts: 'Tab — לשדה הבא לבדיקה · Enter — אישור · 1-3 — בחירת ערך · Ctrl+Z — ביטול',
  expiryWarning: (min: number) => `הנתונים יימחקו אוטומטית בעוד ${min} דקות.`,
  emptyRows: 'עדיין אין שורות להצגה.',
  filteredEmpty: 'אין שורות שדורשות בדיקה.',
  sourceTitle: 'מקור',
} as const;

export const exportScreen = {
  title: 'ייצוא',
  summary: (rows: number, flagged: number) => `${rows} שורות · ${flagged} שדות לבדיקה`,
  warnFlagged: 'נשארו שדות שלא נבדקו. אפשר לייצא, והם יסומנו בכתום בקובץ.',
  xlsx: 'הורדת אקסל',
  csv: 'הורדת CSV',
  json: 'הורדת JSON',
  apiNote: 'אותם נתונים זמינים גם ב-API, לחיבור עתידי למערכות המעבדה.',
  done: 'הקובץ הורד.',
} as const;

export const dashboard = {
  title: 'מדדים',
  kpi: {
    accuracy: 'דיוק',
    silent: 'שגיאה שקטה',
    flagRecall: 'כיסוי סימון',
    localRate: 'נסגר על המחשב',
    calls: 'קריאות לענן',
    time: 'זמן לעמוד',
  },
  ablationTitle: 'מה תרמה כל שכבה',
  learningTitle: 'שיפור בין ריצות',
  stressTitle: 'מצב לחץ',
  stressHint: 'מסיר את הסימון הצהוב, מסובב ומטשטש את העמודים — ובודק שהמנוע ממשיך לעבוד.',
  stressRun: 'הפעילי מצב לחץ',
  measuredNote: 'כל המספרים כאן נמדדו בריצה אמיתית.',
  noData: 'עדיין אין מדידות להצגה.',
} as const;

export const dialogs = {
  shortcutsTitle: 'קיצורי מקלדת',
  shortcutsOpen: 'קיצורי מקלדת',
} as const;
