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
                    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                    "/System/Library/Fonts/Supplemental/Arial.ttf",
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
        value_size = 34 if len(value) >= 6 else 42
        draw.text((x + 24, y + 58), value, font=self.font(value_size), fill=self.TEXT)
        if note:
            draw.text((x + 24, y + 116), note, font=self.font(16), fill=self.MUTED)

    def summary(self, m: dict[str, Any], c: ReportConfig, day: date, out: str):
        image, draw = self.canvas("ЕЖЕДНЕВНЫЙ ОТЧЁТ SLA", day.strftime("%d.%m.%Y") + " · Telemeric Uzum")
        cards = [
            ("Обращения партнёров", str(m["total_tickets"]), "только запросы к поддержке"),
            (
                "Соблюдение SLA",
                "—" if m["sla_percent"] is None else f"{m['sla_percent']:.1f}%",
                f"ответ ≤ {c.sla_target_minutes} мин",
            ),
            (
                "Медиана ответа",
                "—" if m["median_minutes"] is None else f"{m['median_minutes']:.1f} мин",
                "по обращениям с ответом",
            ),
            (
                "Покрытие FAQ",
                "—" if m["faq_coverage_percent"] is None else f"{m['faq_coverage_percent']:.1f}%",
                f"известных тем: {m['faq_covered']}",
            ),
        ]
        for item, x in zip(cards, [70, 440, 810, 1180]):
            self.metric(draw, (x, 195, x + 350, 355), *item)

        self.card(draw, (70, 395, 1000, 900))
        draw.text((100, 425), "Итоговый вывод", font=self.font(28), fill=self.TEXT)
        y = 475
        for line in self.wrap(build_commentary(m, c), 62):
            draw.text((100, y), line, font=self.font(22), fill=self.TEXT)
            y += 34
        criteria_y = max(625, y + 18)
        draw.text((100, criteria_y), "Критерии и сигналы", font=self.font(24), fill=self.TEXT)
        p90 = "—" if m["p90_minutes"] is None else f"{m['p90_minutes']:.1f} мин"
        signals = [
            f"• В SLA ≤ {c.sla_target_minutes} мин: {m['sla_ok']} из {m['total_tickets']}",
            f"• Ответов поддержки: {m['responded']} · без ответа: {m['unanswered']}",
            f"• 90-й перцентиль ответа: {p90}",
            f"• Исключено авто/диалогов/не-запросов: {sum(m['excluded'].values())}",
        ]
        y = criteria_y + 52
        for line in signals:
            draw.text((100, y), line, font=self.font(21), fill=self.TEXT)
            y += 39

        self.card(draw, (1040, 395, 1530, 900))
        draw.text((1070, 425), "Корневые причины", font=self.font(28), fill=self.TEXT)
        cats = [(x["label"], x["count"]) for x in m["root_causes"][:7]] or [("Нет данных", 0)]
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
        image, draw = self.canvas("НАГРУЗКА И ВРЕМЯ ОТВЕТА", day.strftime("%d.%m.%Y") + " · качество смены")
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
        draw.text((1070, 225), "Время первого ответа", font=self.font(28), fill=self.TEXT)
        y = 300
        for label, value in [
            ("Среднее", m["average_minutes"]),
            ("Медиана", m["median_minutes"]),
            ("P90", m["p90_minutes"]),
            ("Норматив SLA", float(c.sla_target_minutes)),
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
        draw.text((100, 625), "Качество обработки смены", font=self.font(28), fill=self.TEXT)
        total = max(1, m["total_tickets"])
        responded_pct = m["responded"] / total * 100
        unanswered_pct = m["unanswered"] / total * 100
        sla_pct = m["sla_percent"] if m["sla_percent"] is not None else 0.0
        quality = [
            ("Получили ответ", m["responded"], responded_pct, self.GREEN),
            (f"В нормативе ≤ {c.sla_target_minutes} мин", m["sla_ok"], sla_pct, self.PURPLE),
            ("Просрочено", len(m["delays"]), (len(m["delays"]) / total * 100), self.RED),
            ("Без ответа", m["unanswered"], unanswered_pct, self.RED),
        ]
        y = 685
        for label, count, pct, color in quality:
            draw.text((100, y), label, font=self.font(21), fill=self.TEXT)
            draw.text((350, y), f"{count}", font=self.font(21), fill=self.TEXT)
            draw.rounded_rectangle((430, y + 3, 1330, y + 29), 11, fill="#ECE6F8")
            width = max(0, min(900, int(900 * pct / 100)))
            if width:
                draw.rounded_rectangle((430, y + 3, 430 + width, y + 29), 11, fill=color)
            draw.text((1360, y), f"{pct:.1f}%", font=self.font(20), fill=self.MUTED)
            y += 52
        draw.text(
            (100, 885),
            "Поддержка: @uzum_franchise · SLA только по обращениям партнёров",
            font=self.font(18),
            fill=self.MUTED,
        )
        image.save(out, "PNG", optimize=True)

    def details(self, m: dict[str, Any], c: ReportConfig, day: date, out: str):
        image, draw = self.canvas(
            "FAQ · КОРНЕВЫЕ ПРИЧИНЫ · РИСКИ",
            day.strftime("%d.%m.%Y") + " · детальный анализ",
        )
        self.card(draw, (70, 195, 760, 905))
        draw.text((100, 225), "Частые обращения партнёров", font=self.font(28), fill=self.TEXT)
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
        draw.text((830, 225), "Анализ корневых причин", font=self.font(28), fill=self.TEXT)
        y = 280
        causes = m["root_causes"][:5] or [{"label": "Нет квалифицированных обращений", "count": 0, "percent": 0.0}]
        for i, item in enumerate(causes, 1):
            draw.text((830, y), f"{i}. {item['label'][:34]}", font=self.font(19), fill=self.TEXT)
            draw.text((1330, y), f"{item['count']} · {item['percent']:.1f}%", font=self.font(18), fill=self.PURPLE)
            y += 48

        self.card(draw, (800, 575, 1530, 905))
        draw.text((830, 605), f"Нарушения SLA > {c.sla_target_minutes} мин", font=self.font(28), fill=self.TEXT)
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
        if m["unanswered"]:
            draw.text(
                (830, min(y + 12, 855)),
                f"Без первого ответа: {m['unanswered']} — считаются нарушением SLA",
                font=self.font(18),
                fill=self.RED,
            )
        image.save(out, "PNG", optimize=True)
