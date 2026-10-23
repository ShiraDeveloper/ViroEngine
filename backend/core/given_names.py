"""Common given names in Israel, used ONLY to order a name pair two readers agree on.

When two readers read the same two words as the patient's name but in opposite order (a rotated
page, an unlabelled sticker line), the word on this list is the first name. A pair with neither
word or both words on the list is left disputed - the list never invents or changes a word.

Deliberately left out: given names that are also common Israeli FAMILY names (אברהם, יעקב, יוסף,
יצחק, דוד, שמעון, חיים, משה, שמואל, אלון, ברק, מאיר, צור, אורן, נתן, אשר, שחר, חן, גל, טל,
רז, זיו...) - for them the order is not knowable from the word alone. Pure data; no I/O.
"""

# ruff: noqa: RUF001  (Hebrew names are data, not confusable text)

from __future__ import annotations

from typing import Final

from backend.core.normalize import normalize_name

_RAW: Final = """
אביגיל אביה אביטל אביתר אביאל אבישי אבנר אדוה אדיר אהובה אהוד אודיה אודליה אוהד אוסנת אופיר
אופק אור אורה אורטל אורי אוריאל אוריה אורית אורלי אושר אושרת אחינועם איה איילה איילת איתי
איתמר איתן אלה אלונה אליאור אליאנה אליה אליהו אליזבט אלינור אלירן אלישבע אלכס אלכסנדר
אלכסנדרה אלמוג אלנה אלעד אמיר אמירה אמית אנה אסף אסתר אפרת אריאל אריאלה בועז בנימין בתאל
גאיה גבריאל גדעון גיא גילה גילי גלית דבורה דביר דורית דורון דנה דני דניאל דניאלה דנית דפנה
דקלה הדס הדר הדסה הודיה הילה הלל הראל הגר ויקטוריה ולדימיר ולנטינה חגי חגית חוה חיה חנה טובה
טליה טלי טניה יגאל ידידיה יהודית יהונתן יהושע יואב יואל יובל יוכבד יוליה יונתן יוני יוסי יעל
יעלי יערה יפית יפעת ירדן ירון ישי ישראל יהלי יוגב כנרת כרמית לאה ליאב ליאור ליאורה ליאל ליאת
לידור ליה ליהי ליזה לילך לימור לינוי לירון לירז ליטל מאור מאיה מוחמד מוריה מורן מיכאל מיכל
מיכאלה מילנה מירב מירי מרים מרינה מתן מתנאל מעיין נגה נדב נויה נועם נועה נחמה נטע נטלי נטליה
ניצן נירית נעמה נעמי נתנאל סבטלנה סיגל סיון ספיר סתיו עדי עדן עדנה עומר עומרי עופרה עידו עידן
עינב עינת ענבל ענבר ענת עמית עמרי פזית פנינה צביה צופיה צחי קרן קורל קטיה רבקה רוני רונית
רועי רותם רות רחל רינה רינת רעות שגיא שגית שולמית שירה שירן שירלי שלומי שלומית שני שקד שרה
שרון שרית תאיר תהילה תום תומר תמר תמרה
אחמד מחמוד מוסטפא עלי חסן איברהים סאמר סאמי ראמי פאדי עביר אמל מנאל סמאח רנא רים לינא רשא
דימא סלים ג'מאל וסים באסל ח'אלד מאג'ד מוראד עבדאללה אימן אנס האני זיאד טארק יאסר כארים נביל
נאסר עמאד שאדי תאמר הדיל חנאן סוהא סמירה פאטמה זינב איאד
"""

GIVEN_NAMES: Final = frozenset(n for word in _RAW.split() if (n := normalize_name(word)))


def is_given_name(value: str) -> bool:
    """True when the whole value is one common given name (finals and spacing normalized)."""
    name = normalize_name(value)
    return name is not None and " " not in name and name in GIVEN_NAMES
