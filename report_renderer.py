from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from analytics_config import ReportConfig
from analytics_engine import Ticket, build_commentary


class ReportRenderer:
    W, H = 1600, 1000
    BG, CARD, TEXT, MUTED = "#F6F3FF", "#FFFFFF", "#1D1533", "#6F6684"
    PURPLE, PURPLE2, BORDER, GREEN, RED = "#6D28D9", "#8B5CF6", "#E7E0F2", "#15803D", "#B42318"

    def __init__(self) -> None:
        self.font_path = next(
            (
                p
                for p in [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
                ]
                if Path(p).exists()
            ),
            None,
        )

    def font(self, size: int) -> ImageFont.ImageFont:
        return ImageFont.truetype(self.font_path, size) if self.font_path else ImageFont.load_default()

    def canvas(self, title: str, subtitle: str):
        image = Image.new("RGB", (self.W, self.H), self.BG)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((70, 55, 1530, 155), 28, fill=self.PURPLE)
        draw.text((105, 77), title, font=self.font(42), fill="white")
        draw.text((105, 125), subtitle, font=self.font(20), fill="#E9DFFF")
        return image, draw

    def card(self, draw, box):
        draw.rounded_rectangle(box, 24, fill=self.CARD, outline=self.BORDER, width=2)

    def wrap(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(text, width, break_long_words=False) or [""]

    def metric(self, draw, box, label: str, value: str, note: str = ""):
        self.card(draw, box)
        x, y, _, _ = box
        draw.text((x + 24, y + 22), label, font=self.font(19), fill=self.MUTED)
        draw.text((x + 24, y + 58), value, font=self.font(42), fill=self.TEXT)
        if note:
            draw.text((x + 24, y + 116), note, font=self.font(16), fill=self.MUTED)

    def summary(self, m: dict[str, Any], c: ReportConfig, day: date, out: str):
        image, draw = self.canvas("DAILY SLA REPORT", day.strftime("%d.%m.%Y") + " · Telemeric Uzum")
        cards = [
            ("Обращения", str(m["total_tickets"]), f"{m['total_messages']} сообщений"),
            (
                "SLA Compliance",
                "—" if m["sla_percent"] is None else f"{m['sla_percent']:.1f}%",
                f"цель ≥ {c.sla_target_percent:.0f}%",
            ),
            (
                "Median response",
                "—" if m["median_minutes"] is None else f"{m['median_minutes']:.1f} мин",
                f"SLA ≤ {c.sla_target_minutes} мин",
            ),
            ("Без ответа", str(m["unanswered"]), f"ответили: {m['responded']}"),
        ]
        for item, x in zip(cards, [70, 440, 810, 1180]):
            self.metric(draw, (x, 195, x + 350, 355), *item)

        self.card(draw, (70, 395, 1000, 900))
        draw.text((100, 425), "Executive summary", font=self.font(28), fill=self.TEXT)
        y = 475
        for line in self.wrap(build_commentary(m, c), 80):
            draw.text((100, y), line, font=self.font(24), fill=self.TEXT)
            y += 38
        draw.text((100, 645), "Ключевые сигналы", font=self.font(24), fill=self.TEXT)
        p90 = "—" if m["p90_minutes"] is None else f"{m['p90_minutes']:.1f} мин"
        signals = [
            f"• Просрочки SLA: {len(m['delays'])}",
            f"• P90 response: {p90}",
            f"• Сотрудники: {len(m['agents'])}",
        ]
        if not m["agent_configured"]:
            signals.append("• ⚠ Список сотрудников пока не настроен")
        y = 695
        for line in signals:
            draw.text((100, y), line, font=self.font(23), fill=self.TEXT)
            y += 42

        self.card(draw, (1040, 395, 1530, 900))
        draw.text((1070, 425), "TOP категорий", font=self.font(28), fill=self.TEXT)
        cats = m["categories"][:7] or [("Нет данных", 0)]
        maximum = max([v for _, v in cats] + [1])
        y = 485
        for label, count in cats:
            draw.text((1070, y), label[:28], font=self.font(19), fill=self.TEXT)
            draw.rounded_rectangle((1070, y + 32, 1450, y + 51), 9, fill="#ECE6F8")
            width = int(380 * count / maximum)
            if width:
                draw.rounded_rectangle((1070, y + 32, 1070 + width, y + 51), 9, fill=self.PURPLE2)
            draw.text((1462, y + 25), str(count), font=self.font(18), fill=self.MUTED)
            y += 58
        image.save(out, "PNG", optimize=True)

    def charts(self, m: dict[str, Any], c: ReportConfig, day: date, out: str):
        image, draw = self.canvas("LOAD & RESPONSE", day.strftime("%d.%m.%Y") + " · нагрузка и SLA")
        self.card(draw, (70, 195, 1000, 555))
        draw.text((100, 225), "Обращения по часам", font=self.font(28), fill=self.TEXT)
        hourly = dict(m["hourly"])
        start = int(c.workday_start[:2])
        end = int(c.workday_end[:2])
        hours = list(range(start, end + 1))
        maximum = max([hourly.get(h, 0) for h in hours] + [1])
        x1, y1, x2, y2 = 120, 300, 950, 500
        draw.line((x1, y2, x2, y2), fill=self.BORDER, width=2)
        step = (x2 - x1) / max(1, len(hours))
        for i, hour in enumerate(hours):
            count = hourly.get(hour, 0)
            bh = int((y2 - y1 - 30) * count / maximum)
            x = int(x1 + i * step + 8)
            draw.rounded_rectangle((x, y2 - bh, int(x + step - 16), y2), 8, fill=self.PURPLE2)
            draw.text((x, y2 + 8), f"{hour:02d}", font=self.font(15), fill=self.MUTED)
            if count:
                draw.text((x + 5, y2 - bh - 24), str(count), font=self.font(15), fill=self.TEXT)

        self.card(draw, (1040, 195, 1530, 555))
        draw.text((1070, 225), "Response time", font=self.font(28), fill=self.TEXT)
        y = 300
        for label, value in [
            ("Среднее", m["average_minutes"]),
            ("Медиана", m["median_minutes"]),
            ("P90", m["p90_minutes"]),
            ("SLA лимит", float(c.sla_target_minutes)),
        ]:
            draw.text((1070, y), label, font=self.font(21), fill=self.MUTED)
            draw.text(
                (1310, y),
                "—" if value is None else f"{value:.1f} мин",
                font=self.font(23),
                fill=self.TEXT,
            )
            y += 58

        self.card(draw, (70, 595, 1530, 920))
        draw.text((100, 625), "Нагрузка по сотрудникам", font=self.font(28), fill=self.TEXT)
        agents = m["agents"][:8]
        if not agents:
            draw.text(
                (100, 690),
                "Нет данных: настройте ANALYTICS_AGENT_IDS / ANALYTICS_AGENT_USERNAMES",
                font=self.font(22),
                fill=self.MUTED,
            )
        else:
            maximum = max(v for _, v in agents)
            y = 690
            for name, count in agents:
                draw.text((100, y), name[:28], font=self.font(20), fill=self.TEXT)
                draw.rounded_rectangle((450, y + 2, 1320, y + 26), 10, fill="#ECE6F8")
                draw.rounded_rectangle(
                    (450, y + 2, 450 + int(870 * count / maximum), y + 26),
                    10,
                    fill=self.PURPLE,
                )
                draw.text((1350, y), str(count), font=self.font(19), fill=self.TEXT)
                y += 40
        image.save(out, "PNG", optimize=True)

    def details(self, m: dict[str, Any], c: ReportConfig, day: date, out: str):
        image, draw = self.canvas(
            "QUESTIONS · SOLUTIONS · RISKS",
            day.strftime("%d.%m.%Y") + " · детальный разбор",
        )
        self.card(draw, (70, 195, 760, 905))
        draw.text((100, 225), "TOP вопросов", font=self.font(28), fill=self.TEXT)
        y = 275
        for i, item in enumerate(
            m["top_questions"][:6] or [{"text": "Нет повторяющихся вопросов", "count": 0}],
            1,
        ):
            draw.text((100, y), f"{i}. ×{item['count']}", font=self.font(19), fill=self.PURPLE)
            y += 30
            for line in self.wrap(item["text"], 52)[:2]:
                draw.text((120, y), line, font=self.font(18), fill=self.TEXT)
                y += 27
            y += 18

        self.card(draw, (800, 195, 1530, 535))
        draw.text((830, 225), "TOP решений", font=self.font(28), fill=self.TEXT)
        y = 280
        for i, item in enumerate(
            m["top_solutions"][:4]
            or [{"text": "Пока нет зафиксированных ответов сотрудников", "count": 0}],
            1,
        ):
            draw.text((830, y), f"{i}. ×{item['count']}", font=self.font(18), fill=self.PURPLE)
            for line in self.wrap(item["text"], 52)[:2]:
                draw.text((920, y), line, font=self.font(17), fill=self.TEXT)
                y += 24
            y += 18

        self.card(draw, (800, 575, 1530, 905))
        draw.text((830, 605), "SLA-просрочки", font=self.font(28), fill=self.TEXT)
        y = 660
        delays: list[Ticket] = m["delays"]
        if not delays:
            draw.text((830, y), "Критичных просрочек не найдено", font=self.font(21), fill=self.GREEN)
        for ticket in delays[:5]:
            draw.text(
                (830, y),
                f"#{ticket.id} · {ticket.customer_name[:18]} · {(ticket.response_minutes or 0):.1f} мин",
                font=self.font(19),
                fill=self.RED,
            )
            question = " ".join(str(x.get("text") or "") for x in ticket.messages)
            draw.text((850, y + 28), self.wrap(question, 62)[0], font=self.font(16), fill=self.MUTED)
            y += 67
        image.save(out, "PNG", optimize=True)
