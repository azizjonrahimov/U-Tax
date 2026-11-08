import pytest
from split_tasks import split_tasks, TASKS_MAX

CASES = [
    ("Advertise our firm on X and LinkedIn", ["Advertise our firm on X and LinkedIn"]),
    ("hire 2 people", ["Hire 2 people"]),
    ("Advertise the firm, hire 2 people and follow up with Team #1",
     ["Advertise the firm", "Hire 2 people", "Follow up with Team #1"]),
    ("Get an outside designer agent to make a share card for our franchise tax result, then post it on X and LinkedIn.",
     ["Get an outside designer agent to make a share card for our franchise tax result, post it on X and LinkedIn"]),
    ("1. advertise the firm 2. hire 2 people 3. email our clients",
     ["Advertise the firm", "Hire 2 people", "Email our clients"]),
    ("follow up with Team #1 to check they received their memo. Then hire two people.",
     ["Follow up with Team #1 to check they received their memo", "Hire two people"]),
    ("I want you to advertise our company. Also get an outside designer to make a share card. And email our clients.",
     ["Advertise our company", "Get an outside designer to make a share card", "Email our clients"]),
    ("email Team #1 and check they got the memo", ["Email Team #1, check they got the memo"]),
    ("advertise the firm; hire 3 accountants; post the share card; brief me",
     ["Advertise the firm", "Hire 3 accountants", "Post the share card", "Brief me"]),
    ("So getting an outside designer to make a shared card.", ["Getting an outside designer to make a shared card"]),
    ("Advertise the firm. Make it about the free checkup.", ["Advertise the firm, make it about the free checkup"]),
    ("give me the status updates about hiring and what's the calendar scheduled for",
     ["Give me the status updates about hiring and what's the calendar scheduled for"]),
    ("First, advertise the firm. Second, hire two people. Third, email our clients. Finally, brief me.",
     ["Advertise the firm", "Hire two people", "Email our clients", "Brief me"]),
    ("advertise the firm and advertise the firm", ["Advertise the firm"]),
    ("tax and accounting firm news: advertise it", ["Tax and accounting firm news: advertise it"]),
]


@pytest.mark.parametrize("text,want", CASES)
def test_split(text, want):
    assert split_tasks(text) == want


def test_cap():
    many = "; ".join(f"post update {i}" for i in range(10))
    assert len(split_tasks(many)) == TASKS_MAX


def test_empty():
    assert split_tasks("   ") == []
