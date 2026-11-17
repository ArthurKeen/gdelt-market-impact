"""Plain-English questions, read into the form — by rule, not by a model.

"What if the Houthis attack Saudi Aramco?" becomes Yemen / attacks with military
force / Saudi Arabia, named group Houthis, target Saudi Aramco, over 5 days — and
that reading is shown back before anything is measured, so a misread question is
visible rather than silently answered.

Rules rather than an LLM, deliberately. Explainability is a hard requirement,
and a deterministic reading can be inspected, tested and argued with. It also
means the app needs no model API key to run. The
vocabulary here is small because the form is small — eleven actors, ten actions,
four horizons — so a controlled vocabulary covers it without guessing.
"""
from __future__ import annotations

import datetime as dt
import re

HORIZONS = (1, 3, 5, 10)

# Ordered (pattern, code). A pattern is matched case-insensitively as whole words,
# except where it is spelled in capitals ("US"), which is matched exactly so the
# pronoun "us" does not become the United States.
ACTORS = [
    (r"united states|USA|US|america|american|washington|pentagon|white house", "USA"),
    (r"china|chinese|beijing|PRC", "CHN"),
    (r"iran|iranian|tehran", "IRN"),
    (r"saudi arabia|saudi|saudis|riyadh|KSA", "SAU"),
    (r"russia|russian|moscow|kremlin", "RUS"),
    (r"israel|israeli|idf", "ISR"),
    (r"yemen|yemeni|sanaa", "YEM"),
    (r"lebanon|lebanese|beirut", "LBN"),
    (r"syria|syrian|damascus", "SYR"),
    (r"iraq|iraqi|baghdad", "IRQ"),
    (r"palestine|palestinian|gaza|west bank", "PSE"),
]

# Named groups resolve to the actor GDELT files them under — it has no code for a
# non-state group — and narrow by the group named in the coverage.
GROUPS = [
    (r"houthis?|ansar allah", "Houthis", "YEM"),
    (r"hezbollah|hizbullah", "Hezbollah", "LBN"),
    (r"hamas", "Hamas", "PSE"),
    (r"irgc|quds force|revolutionary guards?", "IRGC / Quds", "IRN"),
    (r"aqap|al[- ]qaeda", "AQAP / al-Qaeda", "YEM"),
]

# Most specific first: "air strike" must win over "strike".
ACTIONS = [
    (r"air ?strikes?|drones?|missiles?|bomb(?:s|ing|ed)?|from the air", "195"),
    (r"small arms|gunfire|firefights?|clash(?:es|ed)?", "193"),
    (r"suicide|terror(?:ist|ism)?|hijack\w*|unconventional", "180"),
    (r"blockades?|blockading|restrict\w*|close[sd]? (?:the )?(?:strait|hormuz)", "172"),
    (r"arrests?|detain\w*|seiz\w* (?:people|crew|sailors)", "173"),
    (r"sanction\w*|embargo\w*|tariffs?|boycott\w*", "163"),
    (r"cuts? ties|downgrad\w* relations|reduc\w* relations|expel\w*", "160"),
    (r"threat\w*", "138"),
    (r"accus\w*|blames?d?", "112"),
    (r"attack\w*|strikes?|struck|invad\w*|invasion|military force|war on|hits?", "190"),
]
ACTION_LABEL = {"163": "imposes sanctions / embargo on", "160": "reduces relations with",
                "138": "threatens", "112": "accuses",
                "190": "attacks with military force", "195": "attacks from the air",
                "193": "fights with small arms", "180": "attacks unconventionally",
                "172": "blockades / restricts movement", "173": "arrests / detains people from"}

# Named events, so "what happened at Abqaiq" needs no date. Every date here must
# be one of the app's episodes; tests/test_pure.py holds that.
EPISODES = [
    (r"abqaiq|khurais", "2019-09-14"),
    (r"price war", "2020-03-09"),
    (r"soleimani", "2020-01-03"),
    (r"stena impero", "2019-07-19"),
    (r"fujairah", "2019-05-12"),
    (r"gulf of oman", "2019-06-13"),
    (r"drone (?:was )?shot down", "2019-06-20"),
    (r"invasion of ukraine|invade[sd]? ukraine", "2022-02-24"),
    (r"ban on russian oil", "2022-03-08"),
    (r"tankers? (?:ablaze|on fire)|seven tankers", "2026-10-22"),
    (r"section 301", "2018-03-22"),
]

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
NUMBER = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}
COUNTRY = {"USA": "the US", "CHN": "China", "IRN": "Iran", "SAU": "Saudi Arabia",
           "RUS": "Russia", "ISR": "Israel", "YEM": "Yemen", "LBN": "Lebanon",
           "PSE": "Palestine", "SYR": "Syria", "IRQ": "Iraq"}


def _rx(pattern: str) -> re.Pattern:
    """Whole-word match; case-insensitive unless the pattern is all capitals."""
    parts = pattern.split("|")
    exact = [p for p in parts if p.isupper()]
    loose = [p for p in parts if not p.isupper()]
    alts = []
    if loose:
        alts.append(rf"(?i:\b(?:{'|'.join(loose)})\b)")
    if exact:
        alts.append(rf"\b(?:{'|'.join(exact)})\b")
    return re.compile("|".join(alts))


def _first(text: str, table) -> tuple[int, tuple] | None:
    """The earliest mention in the text."""
    hits = [(m.start(), row) for row in table for m in [_rx(row[0]).search(text)] if m]
    return min(hits, key=lambda h: h[0]) if hits else None


def _most_specific(text: str, table) -> tuple[int, tuple] | None:
    """The first row of the table that matches anywhere — the table is ordered
    most specific first, so "attack with drones" reads as an air attack."""
    for row in table:
        m = _rx(row[0]).search(text)
        if m:
            return m.start(), row
    return None


def parse_date(text: str) -> str | None:
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        return m.group(0)
    for rx, order in ((rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH},?\s+(\d{{4}})\b", "dmy"),
                      (rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", "mdy")):
        m = re.search(rx, text, re.I)
        if m:
            g = m.groups()
            day, mon, year = (g[0], g[1], g[2]) if order == "dmy" else (g[1], g[0], g[2])
            try:
                return dt.date(int(year), MONTHS[mon[:3].lower()], int(day)).isoformat()
            except ValueError:
                return None
    return None


def parse_horizon(text: str) -> int | None:
    t = text.lower()
    if re.search(r"\b(next day|overnight|tomorrow|that day|same day)\b", t):
        return 1
    if re.search(r"\b(two weeks|fortnight)\b", t):
        return 10
    if re.search(r"\b(a|one|the next|over a) week\b", t):
        return 5
    m = re.search(r"\b(\d+|one|two|three|four|five|ten)\s+(?:trading\s+)?days?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBER[m.group(1)]
        return min(HORIZONS, key=lambda h: (abs(h - n), h))
    return None


def parse(text: str, targets: list[str] | None = None) -> dict:
    """Read a question into the form's fields, and say how it was read."""
    out: dict = {"text": text}
    text = re.sub(r"\bU\.S\.(?:A\.)?", "US", text)     # before \b-anchored matching
    date = parse_date(text)
    episode = _first(text, EPISODES)
    action = _most_specific(text, ACTIONS)
    group = _first(text, GROUPS)
    horizon = parse_horizon(text)

    # Who is mentioned, in order. A group counts as a mention of its country.
    mentions = []
    for pattern, code in ACTORS:
        for m in _rx(pattern).finditer(text):
            mentions.append((m.start(), code))
    if group:
        mentions.append((group[0], group[1][2]))
    mentions.sort()
    order = []
    for _, code in mentions:
        if code not in order:
            order.append(code)
    # "attacked BY Iran": the actor after "by" is the one doing it.
    by = re.search(r"\bby\s+(?:the\s+)?(\w[\w ]*)", text, re.I)
    if by and len(order) == 2:
        tail = by.group(1)
        named_after_by = [c for p, c in ACTORS if _rx(p).match(tail)] + \
            [c for p, _, c in GROUPS if _rx(p).match(tail)]
        if order[1] in named_after_by:
            order.reverse()

    target = None
    low = text.lower()
    for name in sorted(targets or [], key=len, reverse=True):
        if len(name) >= 4 and re.search(rf"\b{re.escape(name.lower())}\b", low):
            target = name
            break

    # A date, or a named event with no action to generalise from, is one day.
    # "What if the Houthis hit Abqaiq again?" has an action, so it stays a class.
    if date or (episode is not None and action is None):
        out.update(mode="event", date=date or episode[1][1], horizon=horizon or 1)
        out["reading"] = (f"One day: {out['date']}, measured over {out['horizon']} "
                          f"trading day{'s' if out['horizon'] > 1 else ''}.")
        return out

    out.update(mode="class", horizon=horizon or 5,
               actorFrom=order[0] if order else None,
               actorTo=order[1] if len(order) > 1 else None,
               cameo=action[1][1] if action else None,
               # The group filter narrows the side DOING it ("named" sits under
               # the first actor). "Israel strikes Hezbollah" names a group on
               # the receiving side, which that filter cannot express.
               group=group[1][1] if group and order and group[1][2] == order[0] else None,
               target=target)
    out["missing"] = [k for k in ("actorFrom", "cameo", "actorTo") if not out[k]]
    # Nothing the corpus can measure: refuse with the reason rather than answer
    # whatever the form happened to hold.
    if not order and not action:
        out["refused"] = ("No country, group or action I can measure. The corpus covers "
                          "events between " + ", ".join(COUNTRY.values())
                          + " — name one or more of them and what happened.")
    who = lambda c: COUNTRY.get(c, "?") if c else "(as set)"
    out["reading"] = (f"A class of events: {who(out['actorFrom'])} "
                      f"{ACTION_LABEL.get(out['cameo'], '(action as set)')} "
                      f"{who(out['actorTo'])}"
                      + (f", named group {out['group']}" if out["group"] else "")
                      + (f", hitting {out['target']}" if out["target"] else "")
                      + f", over {out['horizon']} trading days.")
    return out
