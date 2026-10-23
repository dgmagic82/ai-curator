"""LMS stub: the schedule and student progress from JSON/CSV, behind the functions a real LMS API would expose."""

import csv
import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from curator import config

SOFT_DEADLINE_DAYS = 3  # the FAQ rule: late work is accepted for 3 more days


@lru_cache
def get_schedule(lms_dir: Path = config.LMS_DIR) -> dict:
    return json.loads((lms_dir / "schedule.json").read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get_students(lms_dir: Path = config.LMS_DIR) -> list[dict]:
    return _read_csv(lms_dir / "students.csv")


def get_student(student_id: str, lms_dir: Path = config.LMS_DIR) -> dict:
    return next(s for s in get_students(lms_dir) if s["student_id"] == student_id)


def get_assignments(student_id: str, today: date = config.DEMO_TODAY, lms_dir: Path = config.LMS_DIR) -> list[dict]:
    """Assignments of one student: schedule deadline, personal extension if granted, status, days left."""
    subs = {s["assignment_id"]: s for s in _read_csv(lms_dir / "submissions.csv") if s["student_id"] == student_id}
    result = []
    for a in get_schedule(lms_dir)["assignments"]:
        sub = subs.get(a["assignment_id"], {})
        deadline = datetime.fromisoformat(sub.get("extended_deadline") or a["deadline"])
        result.append({**a,
                       "status": sub.get("status") or "не сдано",
                       "extended": bool(sub.get("extended_deadline")),
                       "effective_deadline": deadline.isoformat(),
                       "days_left": (deadline.date() - today).days,
                       "reviewer_comment": sub.get("reviewer_comment", "")})
    return result


def student_context(student_id: str, today: date = config.DEMO_TODAY) -> str:
    """Plain-text LMS block for the prompt."""
    s = get_student(student_id)
    sched = get_schedule()
    lines = [f"Сегодня: {today:%d.%m.%Y}. Студент: {s['name']} ({s['student_id']}), уровень: {s['level']}, "
             f"{s['cohort']}, использовано продлений: {s['extensions_used']} из 2.",
             f"Курс: {sched['course']['title']}, вебинары: {sched['course']['webinars']}.",
             "Задания студента:"]
    for a in get_assignments(student_id, today):
        when = datetime.fromisoformat(a["effective_deadline"]).strftime("%d.%m.%Y %H:%M МСК")
        extra = " (продлён куратором)" if a["extended"] else ""
        days = a["days_left"]
        left = f"осталось {days} дн." if days >= 0 else f"просрочено на {-days} дн."
        comment = f"; комментарий проверяющего: {a['reviewer_comment']}" if a["reviewer_comment"] else ""
        # date arithmetic is computed here: the model miscounted «24 hours before the deadline» in eval
        request_by = (datetime.fromisoformat(a["effective_deadline"]) - timedelta(hours=24)).strftime("%d.%m.%Y %H:%M МСК")
        extension = f", заявку на продление можно подать до {request_by}" if days >= 1 and a["status"] == "не сдано" else ""
        if days < 0 and a["status"] == "не сдано":
            soft = datetime.fromisoformat(a["effective_deadline"]) + timedelta(days=SOFT_DEADLINE_DAYS)
            extension = (f", мягкий дедлайн до {soft:%d.%m.%Y %H:%M} МСК" if soft.date() >= today else
                         f". ВАЖНО: мягкий дедлайн истёк {soft:%d.%m.%Y}, через обычную форму сдать уже нельзя, "
                         "только по договорённости с куратором-человеком через форму «Заявка куратору»")
        lines.append(f"- {a['title']}: дедлайн {when}{extra}, {left}, статус «{a['status']}»{extension}{comment}")
    upcoming = [w for w in sched.get("webinars", []) if w["date"] >= today.isoformat()][:2]
    if upcoming:
        lines.append("Ближайшие вебинары: " + "; ".join(
            f"{date.fromisoformat(w['date']):%d.%m} {w['time']} «{w['topic']}»" for w in upcoming))
    return "\n".join(lines)
