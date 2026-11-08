"""D41 reference: split one CEO utterance into ordered tasks (deterministic, no LLM).

Copy into backend/app/ceo_tools.py (keep the names). Tested: test_split_tasks.py next to it.
A segment starts a NEW task only if, after filler words are stripped, it begins with an order
verb and does not lean on the previous task ("post it", "check they got it"). Anything else is
glued back onto the task before it, so a single order never gets cut in two.
"""
from __future__ import annotations

import re

TASKS_MAX = 6
TASK_MIN_CHARS, TASK_MAX_CHARS = 3, 300

# verb stems that open an order ("advertise", "hiring", "e-mail", "follow up", ...)
_VERB = (r"(?:advertis|post|shar|hir|find|recruit|e-?mail|send|writ|follow|check|call|ring|"
         r"schedul|book|mak|creat|design|get|buy|run|prepar|fil|comput|review|publish|pay|updat|"
         r"brief|retr|skip|ping|nudg|reach|contact|invit|draft|announc|tell|ask|remind|open|stop|"
         r"pause|resum)\w*")
_FILLER = (r"(?:so|ok(?:ay)?|and|also|then|please|um+|uh+|now|next|plus|first(?:ly)?|second(?:ly)?|"
           r"third(?:ly)?|fourth|finally|lastly|after that|i want you to|i'd like you to|i need you to|"
           r"i want to|can you|could you|would you|let's|let us|we need to|go ahead and|you should)")
_FILLER_RE = re.compile(rf"^(?:{_FILLER})\b[\s,:-]*", re.I)
_LEANS_ON_PREVIOUS = re.compile(r"\b(?:it|them|those|they|their|him|her)\b", re.I)
_STARTS_WITH_VERB = re.compile(rf"^{_VERB}\b", re.I)

# where a new task may begin
_CUT = re.compile(
    r"\s*(?:\n+|;|(?<=[.!?])\s+|(?:^|(?<=\s))\(?\d[.)](?=\s)"
    r"|\b(?:and then|after that|then|also|plus|next|finally|lastly|secondly|thirdly)\b,?"
    rf"|,?\s+and\s+(?={_VERB}\b)|,\s+(?={_VERB}\b))\s*",
    re.I,
)


def _clean(seg: str) -> str:
    seg = seg.strip()
    while True:
        new = _FILLER_RE.sub("", seg).strip()
        if new == seg:
            break
        seg = new
    return seg.strip(" .,;:!-").strip()


def split_tasks(text: str) -> list[str]:
    """Ordered tasks, at most TASKS_MAX. Always at least one item for non-empty text."""
    flat = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not flat:
        return []
    tasks: list[str] = []
    for raw in _CUT.split(flat):
        seg = _clean(raw or "")
        if not seg:
            continue
        first3 = " ".join(seg.split()[:3])
        new_task = (not tasks) or (_STARTS_WITH_VERB.match(seg) and not _LEANS_ON_PREVIOUS.search(first3))
        if new_task:
            tasks.append(seg)
        else:
            if _STARTS_WITH_VERB.match(seg):  # "Make it ..." glued on reads "..., make it ..."
                seg = seg[0].lower() + seg[1:]
            tasks[-1] = f"{tasks[-1]}, {seg}"
    out: list[str] = []
    seen: set[str] = set()
    for t in tasks:
        t = t[:TASK_MAX_CHARS].strip()
        key = t.lower()
        if len(t) >= TASK_MIN_CHARS and key not in seen:
            seen.add(key)
            out.append(t[0].upper() + t[1:])
    return out[:TASKS_MAX] or [flat[:TASK_MAX_CHARS]]
