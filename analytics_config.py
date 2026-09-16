from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ReportConfig:
    source_chat_id: int
    report_chat_id: int
    enabled: bool = False
    timezone: str = "Asia/Tashkent"
    workday_start: str = "10:00"
    workday_end: str = "19:00"
    report_time: str = "19:01"
    sla_target_minutes: int = 15
    sla_target_percent: float = 90.0
    ticket_gap_minutes: int = 30
    agent_ids: set[int] = field(default_factory=set)
    agent_usernames: set[str] = field(default_factory=set)

    @classmethod
    def from_env(cls, source_chat_id: int) -> "ReportConfig":
        ids = {
            int(v.strip())
            for v in os.getenv("ANALYTICS_AGENT_IDS", "").split(",")
            if v.strip().lstrip("-").isdigit()
        }
        usernames = {
            v.strip().lower().lstrip("@")
            for v in os.getenv("ANALYTICS_AGENT_USERNAMES", "uzum_franchise").split(",")
            if v.strip()
        }
        return cls(
            source_chat_id=source_chat_id,
            report_chat_id=int(os.getenv("REPORT_CHAT_ID", str(source_chat_id))),
            enabled=os.getenv("DAILY_REPORT_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            timezone=os.getenv("REPORT_TIMEZONE", "Asia/Tashkent"),
            workday_start=os.getenv("WORKDAY_START", "10:00"),
            workday_end=os.getenv("WORKDAY_END", "19:00"),
            report_time=os.getenv("REPORT_TIME", "19:01"),
            sla_target_minutes=int(os.getenv("SLA_TARGET_MINUTES", "15")),
            sla_target_percent=float(os.getenv("SLA_TARGET_PERCENT", "90")),
            ticket_gap_minutes=int(os.getenv("TICKET_GAP_MINUTES", "30")),
            agent_ids=ids,
            agent_usernames=usernames,
        )
