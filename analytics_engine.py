from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig


@dataclass
class Ticket:
    id: int
    customer_id: int
    customer_name: str
    opened_at: int
    last_customer_at: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    first_response_at: int | None = None
    first_response_text: str = ""
    agent_id: int | None = None
    agent_name: str = ""
    response_inferred: bool = False

    @property
    def response_minutes(self) -> float | None:
        if self.first_response_at is None:
            return None
        return max(0.0, (self.first_response_at - self.opened_at) / 60.0)


CATEGORY_RULES = [
    ("Выплаты / оплата", ("выплат", "оплат", "перевод", "деньг", "баланс", "payment", "tolov", "to'lov")),
    ("Инкассация", ("инкас", "inkass", "касс", "налич", "nalich")),
    ("Переоформление", ("переоформ", "юр лиц", "юрид", "qayta rasm")),
    ("Переезд", ("переезд", "ko'ch", "koch")),
    ("Документы", ("документ", "договор", "аренд", "ижар", "кадастр", "паспорт", "реквиз", "rekvizit")),
    ("Приложение / техника", ("ошиб", "не работает", "не откры", "прилож", "кабинет", "вход", "код", "error", "xato")),
    ("Оборудование", ("банкомат", "терминал", "принтер", "сканер", "оборуд")),
]


def _name(message: dict[str, Any]) -> str:
    return (
        message.get("sender_name")
        or ("@" + message["username"] if message.get("username") else "")
        or str(message.get("sender_id") or "Unknown")
    )


def _username(message: dict[str, Any]) -> str:
    return str(message.get("username") or "").lower().lstrip("@")


def _is_agent(message: dict[str, Any], config: ReportConfig) -> bool:
    username = _username(message)
    return int(message.get("sender_id") or 0) in config.agent_ids or bool(
        username and username in config.agent_usernames
    )


def _is_service_message(message: dict[str, Any], config: ReportConfig) -> bool:
    username = _username(message)
    return bool(username and username in config.service_bot_usernames)


def _canonical(text: str) -> str:
    value = text.lower().replace("ё", "е")
    value = re.sub(r"https?://\S+|[@#]\w+|\b\d{2,}\b", " ", value)
    value = re.sub(r"[^a-zа-я0-9ўқғҳ' ]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()[:180]


def _category(text: str) -> str:
    value = text.lower().replace("ё", "е")
    for label, keywords in CATEGORY_RULES:
        if any(keyword in value for keyword in keywords):
            return label
    return "Другое"


def build_tickets(messages: list[dict[str, Any]], config: ReportConfig) -> list[Ticket]:
    tickets: list[Ticket] = []
    by_message: dict[int, Ticket] = {}
    active: dict[int, Ticket] = {}
    gap = config.ticket_gap_minutes * 60

    for message in messages:
        if _is_service_message(message, config):
            continue
        sender_id = int(message.get("sender_id") or 0)
        if not sender_id:
            continue
        ts = int(message.get("ts") or 0)
        if _is_agent(message, config):
            target = by_message.get(int(message.get("reply_to_message_id") or 0))
            if target is None:
                unresolved = [t for t in tickets if t.first_response_at is None and t.opened_at <= ts]
                target = max(unresolved, key=lambda t: t.last_customer_at) if unresolved else None
            if target and target.first_response_at is None:
                target.first_response_at = ts
                target.first_response_text = str(message.get("text") or "")
                target.agent_id = sender_id
                target.agent_name = _name(message)
                target.response_inferred = not bool(message.get("reply_to_message_id"))
            continue

        ticket = active.get(sender_id)
        if ticket is None or ticket.first_response_at is not None or ts - ticket.last_customer_at > gap:
            ticket = Ticket(len(tickets) + 1, sender_id, _name(message), ts, ts)
            tickets.append(ticket)
            active[sender_id] = ticket
        ticket.messages.append(message)
        ticket.last_customer_at = ts
        if message.get("message_id"):
            by_message[int(message["message_id"])] = ticket
    return tickets


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def build_metrics(messages: list[dict[str, Any]], config: ReportConfig) -> dict[str, Any]:
    effective_messages = [m for m in messages if not _is_service_message(m, config)]
    tickets = build_tickets(effective_messages, config)
    responded = [t for t in tickets if t.response_minutes is not None]
    response_times = [t.response_minutes for t in responded if t.response_minutes is not None]
    sla_ok = [t for t in responded if (t.response_minutes or 0) <= config.sla_target_minutes]
    unanswered = [t for t in tickets if t.first_response_at is None]
    categories: Counter[str] = Counter()
    questions: Counter[str] = Counter()
    solutions: Counter[str] = Counter()
    q_display: dict[str, str] = {}
    s_display: dict[str, str] = {}
    hourly: Counter[int] = Counter()
    agents: Counter[str] = Counter()
    delays: list[Ticket] = []
    tz = ZoneInfo(config.timezone)

    for ticket in tickets:
        question = " ".join(str(m.get("text") or "") for m in ticket.messages).strip()
        categories[_category(question)] += 1
        key = _canonical(question)
        if key:
            questions[key] += 1
            q_display.setdefault(key, question[:220])
        hourly[datetime.fromtimestamp(ticket.opened_at, tz).hour] += 1
        if ticket.first_response_text:
            key = _canonical(ticket.first_response_text)
            if key:
                solutions[key] += 1
                s_display.setdefault(key, ticket.first_response_text[:220])
        if ticket.agent_name:
            agents[ticket.agent_name] += 1
        if ticket.response_minutes is not None and ticket.response_minutes > config.sla_target_minutes:
            delays.append(ticket)

    delays.sort(key=lambda t: t.response_minutes or 0, reverse=True)
    return {
        "tickets": tickets,
        "total_tickets": len(tickets),
        "total_messages": len(effective_messages),
        "responded": len(responded),
        "unanswered": len(unanswered),
        "sla_ok": len(sla_ok),
        "sla_percent": (len(sla_ok) / len(responded) * 100) if responded else None,
        "average_minutes": (sum(response_times) / len(response_times)) if response_times else None,
        "median_minutes": _percentile(response_times, 0.5),
        "p90_minutes": _percentile(response_times, 0.9),
        "categories": categories.most_common(),
        "hourly": sorted(hourly.items()),
        "agents": agents.most_common(),
        "top_questions": [{"text": q_display[k], "count": c} for k, c in questions.most_common(7)],
        "top_solutions": [{"text": s_display[k], "count": c} for k, c in solutions.most_common(5)],
        "delays": delays[:7],
        "agent_configured": bool(config.agent_ids or config.agent_usernames),
    }


def build_commentary(metrics: dict[str, Any], config: ReportConfig) -> str:
    if not metrics["agent_configured"]:
        return (
            "SLA пока не рассчитывается: не настроен общий аккаунт поддержки. "
            "Остальная статистика уже собирается."
        )
    if not metrics["total_tickets"]:
        return "За выбранную смену обращений не найдено."
    sla = metrics["sla_percent"]
    parts = [
        "Не было обращений с зафиксированным ответом поддержки."
        if sla is None
        else f"SLA {'выполнен' if sla >= config.sla_target_percent else 'ниже цели'}: {sla:.1f}% при цели {config.sla_target_percent:.0f}%."
    ]
    if metrics["categories"]:
        label, count = metrics["categories"][0]
        parts.append(f"Главная тема — «{label}» ({count} обращ.).")
    if metrics["hourly"]:
        hour, count = max(metrics["hourly"], key=lambda x: x[1])
        parts.append(f"Пик — {hour:02d}:00–{(hour + 1) % 24:02d}:00 ({count} обращ.).")
    if metrics["unanswered"]:
        parts.append(f"Без первого ответа: {metrics['unanswered']}.")
    if metrics["delays"]:
        parts.append(f"Нужно разобрать {len(metrics['delays'])} самых долгих SLA-просрочек.")
    return " ".join(parts)
