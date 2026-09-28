from __future__ import annotations

import html
import json
import logging
import math
import re
import time
from collections import Counter, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


logger = logging.getLogger("telemeric.faq-shadow")

TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "а",
    "без",
    "билан",
    "бу",
    "бы",
    "бўйича",
    "в",
    "ва",
    "вам",
    "ваш",
    "вы",
    "где",
    "для",
    "до",
    "его",
    "если",
    "за",
    "и",
    "из",
    "или",
    "как",
    "какой",
    "кимга",
    "ли",
    "могу",
    "можно",
    "мне",
    "мой",
    "на",
    "не",
    "нет",
    "нима",
    "но",
    "o'z",
    "по",
    "почему",
    "при",
    "с",
    "siz",
    "у",
    "учун",
    "что",
    "это",
    "я",
    "qanday",
    "qilib",
    "kerak",
}
TOKEN_ALIASES = {
    "бтп": "пвз",
    "btp": "пвз",
    "pvz": "пвз",
    "пункт": "пвз",
    "пункта": "пвз",
    "пункте": "пвз",
    "пунктом": "пвз",
    "возврат": "возврат",
    "возврата": "возврат",
    "возвратить": "возврат",
    "вернуть": "возврат",
    "виден": "отображается",
    "видно": "отображается",
    "показывается": "отображается",
    "карте": "карта",
    "карту": "карта",
    "реквизит": "реквизиты",
    "реквизитов": "реквизиты",
    "реквизитами": "реквизиты",
    "инкассатора": "инкассатор",
    "инкассации": "инкассация",
    "инкассацию": "инкассация",
    "электричества": "электроэнергия",
    "света": "электроэнергия",
}


def normalize_text(value: str) -> str:
    value = (
        value.casefold()
        .replace("ё", "е")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
    )
    return " ".join(TOKEN_RE.findall(value))


def tokenize(value: str) -> set[str]:
    tokens = set()
    for token in TOKEN_RE.findall(normalize_text(value)):
        token = TOKEN_ALIASES.get(token, token)
        if len(token) >= 3 and token not in STOP_WORDS:
            tokens.add(token)
    return tokens


@dataclass(frozen=True)
class FAQMatch:
    entry: dict[str, Any]
    confidence: float
    language: str
    runner_up_confidence: float


class FAQKnowledgeBase:
    def __init__(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.entries = [entry for entry in payload.get("entries", []) if entry.get("active")]
        if not self.entries:
            raise ValueError("FAQ knowledge base is empty")

        variants: list[tuple[dict[str, Any], str, str, set[str]]] = []
        document_frequency: Counter[str] = Counter()
        for entry in self.entries:
            entry_tokens: set[str] = set()
            for language in ("ru", "uz"):
                question = str(entry.get(f"question_{language}") or "").strip()
                if not question:
                    continue
                tokens = tokenize(question)
                variants.append((entry, language, normalize_text(question), tokens))
                entry_tokens.update(tokens)
            document_frequency.update(entry_tokens)

        self.variants = variants
        total = len(self.entries)
        self.idf = {
            token: math.log((total + 1) / (frequency + 1)) + 1
            for token, frequency in document_frequency.items()
        }

    def _score(self, query: str, query_tokens: set[str], title: str, title_tokens: set[str]) -> float:
        common = query_tokens & title_tokens
        if not common:
            return 0.0
        query_weight = sum(self.idf.get(token, 1.0) for token in query_tokens) or 1.0
        overlap_weight = sum(self.idf.get(token, 1.0) for token in common)
        query_coverage = overlap_weight / query_weight
        title_coverage = len(common) / max(1, min(len(query_tokens), len(title_tokens)))
        sequence = SequenceMatcher(None, query, title).ratio()
        score = 0.68 * query_coverage + 0.20 * title_coverage + 0.12 * sequence
        if query in title or title in query:
            score += 0.08
        return min(score, 1.0)

    def match(self, question: str, threshold: float = 0.58) -> FAQMatch | None:
        query = normalize_text(question)
        query_tokens = tokenize(question)
        if not query_tokens:
            return None

        scored: list[tuple[float, dict[str, Any], str]] = []
        for entry, language, title, title_tokens in self.variants:
            score = self._score(query, query_tokens, title, title_tokens)
            if score:
                scored.append((score, entry, language))
        if not scored:
            return None

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_entry, best_language = scored[0]
        runner_up = next(
            (score for score, entry, _ in scored[1:] if entry["id"] != best_entry["id"]),
            0.0,
        )
        if best_score < threshold:
            return None
        if len(query_tokens) == 1 and best_score < max(0.72, threshold):
            return None
        if runner_up >= threshold and best_score - runner_up < 0.08:
            return None
        return FAQMatch(best_entry, best_score, best_language, runner_up)


class FAQShadowAssistant:
    """Suggest approved FAQ answers privately without writing to the source group."""

    def __init__(
        self,
        knowledge_base: FAQKnowledgeBase,
        sender: Any,
        source_chat_id: int,
        recipient_id: int,
        threshold: float = 0.58,
        max_age_seconds: int = 600,
        ignored_usernames: set[str] | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.sender = sender
        self.source_chat_id = source_chat_id
        self.recipient_id = recipient_id
        self.threshold = threshold
        self.max_age_seconds = max_age_seconds
        self.ignored_usernames = {value.casefold().lstrip("@") for value in ignored_usernames or set()}
        self._seen: set[tuple[int, int]] = set()
        self._seen_order: deque[tuple[int, int]] = deque()

    @property
    def entry_count(self) -> int:
        return len(self.knowledge_base.entries)

    def _remember(self, key: tuple[int, int]) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        self._seen_order.append(key)
        while len(self._seen_order) > 5000:
            self._seen.discard(self._seen_order.popleft())
        return True

    def _is_live_partner_message(self, message: dict[str, Any]) -> bool:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if int(chat.get("id") or 0) != self.source_chat_id:
            return False
        if sender.get("is_bot") or message.get("sender_chat"):
            return False
        username = str(sender.get("username") or "").casefold().lstrip("@")
        if username and username in self.ignored_usernames:
            return False
        timestamp = int(message.get("date") or 0)
        if timestamp and abs(int(time.time()) - timestamp) > self.max_age_seconds:
            return False
        text = str(message.get("text") or message.get("caption") or "").strip()
        return len(text) >= 4

    def evaluate(self, question: str) -> FAQMatch | None:
        return self.knowledge_base.match(question, self.threshold)

    def _source_link(self, chat_id: int, message_id: int) -> str:
        raw = str(abs(chat_id))
        internal_id = raw[3:] if raw.startswith("100") else raw
        return f"https://t.me/c/{internal_id}/{message_id}"

    def format_preview(
        self,
        question: str,
        match: FAQMatch,
        sender_label: str = "Тестовый запрос",
        source_link: str = "",
    ) -> str:
        entry = match.entry
        language = match.language
        answer = str(entry.get(f"answer_{language}") or entry.get("answer_ru") or "").strip()
        title = str(entry.get(f"question_{language}") or entry.get("question_ru") or "").strip()
        notes = entry.get("internal_notes") or []
        review_hint = ""
        if notes:
            review_hint = "\n\n⚠️ <b>Есть подсказка оператору:</b> проверьте ответ перед использованием."
        if any(marker in answer.casefold() for marker in ("ссылка на канал", "видео инструкция", "презентац")) and "http" not in answer:
            review_hint += "\n⚠️ <b>В скрипте отсутствует ссылка или вложение.</b>"
        link_line = f'\n<a href="{html.escape(source_link, quote=True)}">Открыть сообщение в группе</a>' if source_link else ""
        return (
            "🧪 <b>Тестовый ответ FAQ-бота</b>\n\n"
            f"<b>Автор:</b> {html.escape(sender_label)}\n"
            f"<b>Тема #{entry['id']}:</b> {html.escape(title)}\n"
            f"<b>Уверенность:</b> {match.confidence * 100:.0f}%\n"
            f"<b>Вопрос:</b> {html.escape(question)}{link_line}\n\n"
            f"<b>Предлагаемый ответ:</b>\n{html.escape(answer)}"
            f"{review_hint}"
        )

    async def send_test_question(self, question: str, sender_label: str = "Ручной тест") -> FAQMatch:
        match = self.evaluate(question)
        if match is None:
            raise ValueError("Подходящий утверждённый ответ не найден")
        await self.sender.send_message(
            self.recipient_id,
            self.format_preview(question, match, sender_label=sender_label),
        )
        return match

    async def handle_updates(self, updates: list[dict[str, Any]]) -> int:
        sent = 0
        for update in updates:
            message = update.get("message") or update.get("edited_message") or {}
            if not self._is_live_partner_message(message):
                continue
            chat_id = int((message.get("chat") or {}).get("id") or 0)
            message_id = int(message.get("message_id") or 0)
            if not message_id or not self._remember((chat_id, message_id)):
                continue
            question = str(message.get("text") or message.get("caption") or "").strip()
            match = self.evaluate(question)
            if match is None:
                continue
            sender = message.get("from") or {}
            username = str(sender.get("username") or "").strip()
            name = " ".join(
                str(sender.get(field) or "").strip()
                for field in ("first_name", "last_name")
                if sender.get(field)
            ).strip()
            sender_label = f"@{username}" if username else (name or str(sender.get("id") or "Партнёр"))
            source_link = self._source_link(chat_id, message_id)
            try:
                await self.sender.send_message(
                    self.recipient_id,
                    self.format_preview(question, match, sender_label, source_link),
                )
                sent += 1
            except Exception:
                logger.exception("FAQ shadow preview delivery failed for message %s", message_id)
        return sent
