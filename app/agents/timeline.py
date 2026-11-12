"""Redressal timelines stated in a passage ("within 21 calendar days", "not later than twenty-one calendar days").

Shared by the rule engine (to propose a timeline) and the Verifier (to check that a proposed timeline is stated in a
cited servicing passage). Regulations write numbers both as digits and as words, so both are parsed."""
import re
from typing import List, Optional, Tuple

_UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
          "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_NUM = r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?|" \
            r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|" \
            r"seventeen|eighteen|nineteen)"
TL_RE = re.compile(r"(?:within|not\s+later\s+than|no\s+later\s+than)\s+(?:a\s+period\s+of\s+)?(\d{1,3}|" + _WORD_NUM +
                   r")\s+(?:\(\d+\)\s+)?(?:calendar\s+|working\s+)?days", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z(])")


def word_to_int(tok: str) -> Optional[int]:
    tok = tok.lower().strip()
    if tok.isdigit():
        return int(tok)
    parts = re.split(r"[\s-]+", tok)
    if len(parts) == 1:
        return _UNITS.get(parts[0], _TENS.get(parts[0]))
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS:
        return _TENS[parts[0]] + _UNITS[parts[1]]
    return None


def timelines(text: str) -> List[Tuple[int, str]]:
    """All (days, sentence) pairs stated in the text."""
    out = []
    for sent in _SENT_SPLIT.split(text or ""):
        for m in TL_RE.finditer(sent):
            n = word_to_int(m.group(1))
            if n:
                out.append((n, sent.strip()))
    return out


def days_stated(text: str) -> set:
    return {n for n, _ in timelines(text)}
