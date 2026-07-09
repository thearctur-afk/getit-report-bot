# -*- coding: utf-8 -*-
"""
Работа с SQLite: менеджеры, ежедневные активности (план/факт) и
состояние пошагового опросника.

Соединение открывается на каждый вызов — для нагрузки в несколько
десятков менеджеров и пары сообщений в день это не проблема, зато
не нужно думать о потокобезопасности одного общего коннекта.
"""
import json
import re
import sqlite3
from datetime import datetime, date as date_cls, timedelta
from typing import Optional

import config

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Создать таблицы, если их ещё нет. Вызывается один раз при старте."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS managers (
            telegram_id INTEGER PRIMARY KEY,
            manager_id  TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            role        TEXT NOT NULL,
            username    TEXT,
            active      INTEGER NOT NULL DEFAULT 1,
            registered  TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            manager_id  TEXT NOT NULL,
            date        TEXT NOT NULL,
            field_key   TEXT NOT NULL,
            plan        REAL NOT NULL DEFAULT 0,
            fact        REAL NOT NULL DEFAULT 0,
            updated_at  TEXT NOT NULL,
            UNIQUE(manager_id, date, field_key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS survey_state (
            telegram_id INTEGER PRIMARY KEY,
            manager_id  TEXT NOT NULL,
            date        TEXT NOT NULL,
            current_step INTEGER NOT NULL DEFAULT 0,
            answers     TEXT NOT NULL DEFAULT '{}',
            started_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Менеджеры
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    lowered = name.strip().lower()
    translit = "".join(_TRANSLIT.get(ch, ch) for ch in lowered)
    slug = re.sub(r"[^a-z0-9]+", "_", translit).strip("_")
    return slug or "manager"


def generate_manager_id(name: str) -> str:
    """
    Автогенерация manager_id из ФИО (транслитерация + слаг).
    Если такой id уже есть (тёзки) — добавляем числовой суффикс.
    """
    conn = _connect()
    cur = conn.cursor()
    base = _slugify(name)
    candidate = base
    i = 1
    while True:
        cur.execute("SELECT 1 FROM managers WHERE manager_id = ?", (candidate,))
        if cur.fetchone() is None:
            conn.close()
            return candidate
        i += 1
        candidate = f"{base}_{i}"


def add_manager(telegram_id: int, manager_id: str, name: str, role: str, username: Optional[str]) -> None:
    conn = _connect()
    conn.execute(
        """INSERT INTO managers (telegram_id, manager_id, name, role, username, active, registered)
           VALUES (?, ?, ?, ?, ?, 1, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
             manager_id=excluded.manager_id,
             name=excluded.name,
             role=excluded.role,
             username=excluded.username,
             active=1""",
        (telegram_id, manager_id, name, role, username, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def get_manager_by_telegram_id(telegram_id: int) -> Optional[sqlite3.Row]:
    conn = _connect()
    row = conn.execute("SELECT * FROM managers WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return row


def get_manager_by_manager_id(manager_id: str) -> Optional[sqlite3.Row]:
    conn = _connect()
    row = conn.execute("SELECT * FROM managers WHERE manager_id = ?", (manager_id,)).fetchone()
    conn.close()
    return row


def get_all_managers(active_only: bool = True):
    conn = _connect()
    if active_only:
        rows = conn.execute("SELECT * FROM managers WHERE active = 1 ORDER BY name").fetchall()
    else:
        rows = conn.execute("SELECT * FROM managers ORDER BY name").fetchall()
    conn.close()
    return rows


def set_manager_active(manager_id: str, active: bool) -> None:
    conn = _connect()
    conn.execute("UPDATE managers SET active = ? WHERE manager_id = ?", (1 if active else 0, manager_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Активности (план / факт)
# ---------------------------------------------------------------------------
def upsert_activity(manager_id: str, date: str, field_key: str,
                     fact: Optional[float] = None, plan: Optional[float] = None) -> None:
    """
    Обновить факт и/или план по конкретному полю за конкретный день.
    Если строки ещё нет — создаём её (недостающее значение = 0).
    """
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    existing = conn.execute(
        "SELECT plan, fact FROM activities WHERE manager_id=? AND date=? AND field_key=?",
        (manager_id, date, field_key),
    ).fetchone()

    new_plan = plan if plan is not None else (existing["plan"] if existing else 0)
    new_fact = fact if fact is not None else (existing["fact"] if existing else 0)

    conn.execute(
        """INSERT INTO activities (manager_id, date, field_key, plan, fact, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(manager_id, date, field_key) DO UPDATE SET
             plan = excluded.plan,
             fact = excluded.fact,
             updated_at = excluded.updated_at""",
        (manager_id, date, field_key, new_plan, new_fact, now),
    )
    conn.commit()
    conn.close()


def set_plan(manager_id: str, field_key: str, plan: float, date: Optional[str] = None,
             propagate_to_month_end: bool = True) -> int:
    """
    Установить план менеджеру по полю.

    По умолчанию план проставляется не только на указанный день, но и на все
    оставшиеся дни текущего месяца — так план не "сгорает" на следующий день
    и не нужно выставлять его заново каждое утро. Возвращает количество дней,
    на которые план был записан.
    """
    start = datetime.strptime(date, "%Y-%m-%d").date() if date else date_cls.today()

    if not propagate_to_month_end:
        upsert_activity(manager_id, start.isoformat(), field_key, plan=plan)
        return 1

    if start.month == 12:
        next_month_first = date_cls(start.year + 1, 1, 1)
    else:
        next_month_first = date_cls(start.year, start.month + 1, 1)
    month_end = next_month_first - timedelta(days=1)

    days_written = 0
    current = start
    while current <= month_end:
        upsert_activity(manager_id, current.isoformat(), field_key, plan=plan)
        current += timedelta(days=1)
        days_written += 1
    return days_written


def get_raw_activities(start: str, end: str):
    """Плоский список всех записей activities за период — для выгрузки в Excel."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM activities WHERE date BETWEEN ? AND ? ORDER BY date, manager_id, field_key",
        (start, end),
    ).fetchall()
    conn.close()
    return rows


def get_activities_for_date(date: str) -> dict:
    """{manager_id: {field_key: {"plan": x, "fact": y}}}"""
    conn = _connect()
    rows = conn.execute("SELECT * FROM activities WHERE date = ?", (date,)).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        result.setdefault(r["manager_id"], {})[r["field_key"]] = {"plan": r["plan"], "fact": r["fact"]}
    return result


def get_activities_for_period(start: str, end: str) -> dict:
    """Суммы факта и максимум плана по каждому менеджеру/полю за период [start, end]."""
    conn = _connect()
    rows = conn.execute(
        """SELECT manager_id, field_key, SUM(fact) as fact_sum, SUM(plan) as plan_sum
           FROM activities WHERE date BETWEEN ? AND ?
           GROUP BY manager_id, field_key""",
        (start, end),
    ).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        result.setdefault(r["manager_id"], {})[r["field_key"]] = {
            "plan": r["plan_sum"] or 0,
            "fact": r["fact_sum"] or 0,
        }
    return result


def has_reported(manager_id: str, date: str) -> bool:
    """Заполнил ли менеджер отчёт за эту дату (есть хотя бы одна запись)."""
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*) as c FROM activities WHERE manager_id=? AND date=?",
        (manager_id, date),
    ).fetchone()
    conn.close()
    return bool(row and row["c"] > 0)


# ---------------------------------------------------------------------------
# Состояние опросника (survey_state) — на случай, если менеджер прервался
# на середине заполнения (закрыл чат, перезапустился бот и т.п.)
# ---------------------------------------------------------------------------
def start_survey(telegram_id: int, manager_id: str, date: str) -> None:
    conn = _connect()
    conn.execute(
        """INSERT INTO survey_state (telegram_id, manager_id, date, current_step, answers, started_at)
           VALUES (?, ?, ?, 0, '{}', ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
             manager_id=excluded.manager_id,
             date=excluded.date,
             current_step=0,
             answers='{}',
             started_at=excluded.started_at""",
        (telegram_id, manager_id, date, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def get_survey_state(telegram_id: int) -> Optional[dict]:
    conn = _connect()
    row = conn.execute("SELECT * FROM survey_state WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "telegram_id": row["telegram_id"],
        "manager_id": row["manager_id"],
        "date": row["date"],
        "current_step": row["current_step"],
        "answers": json.loads(row["answers"]),
    }


def update_survey_state(telegram_id: int, current_step: int, answers: dict) -> None:
    conn = _connect()
    conn.execute(
        "UPDATE survey_state SET current_step = ?, answers = ? WHERE telegram_id = ?",
        (current_step, json.dumps(answers, ensure_ascii=False), telegram_id),
    )
    conn.commit()
    conn.close()


def clear_survey_state(telegram_id: int) -> None:
    conn = _connect()
    conn.execute("DELETE FROM survey_state WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
