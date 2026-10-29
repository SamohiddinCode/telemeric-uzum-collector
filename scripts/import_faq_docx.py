from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": WORD_NS}


def read_paragraphs(path: Path) -> list[str]:
    """Extract visible paragraph text from a DOCX without runtime dependencies."""
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))

    paragraphs: list[str] = []
    for paragraph in document.findall(".//w:body/w:p", NS):
        chunks: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{{{WORD_NS}}}t":
                chunks.append(node.text or "")
            elif node.tag in {f"{{{WORD_NS}}}br", f"{{{WORD_NS}}}cr"}:
                chunks.append("\n")
            elif node.tag == f"{{{WORD_NS}}}tab":
                chunks.append("\t")
        text = "".join(chunks).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def question_number(text: str, expected: int) -> int | None:
    match = re.match(r"^\s*(\d+)\s*[.#]?", text)
    if not match:
        return None
    number = int(match.group(1))
    return number if number == expected else None


def clean_question(text: str, number: int) -> tuple[str, str]:
    text = re.sub(rf"^\s*{number}\s*[.#]?\s*", "", text).strip()
    text = re.sub(r"\s*\(\s*1\s*\)\s*$", "", text).strip()
    text = re.sub(r"\s*\(\s*Неактуально\s*\)\s*$", "", text, flags=re.I).strip()
    if "/" in text:
        russian, uzbek = text.split("/", 1)
    else:
        russian, uzbek = text, ""
    russian = russian.strip(" #\t")
    uzbek = uzbek.strip(" #\t")
    uzbek = re.sub(r"\s*\(\s*1\s*\).*?$", "", uzbek).strip()
    uzbek = re.sub(r"\s*\(\s*у нас регламент.*\)\s*$", "", uzbek, flags=re.I).strip()
    return russian, uzbek


def language_marker(text: str) -> str | None:
    normalized = text.casefold().replace("’", "'").replace("‘", "'").replace("`", "'")
    if "русск" in normalized or normalized.startswith("ru:"):
        return "ru"
    if "o'zbek" in normalized or "oz'bek" in normalized or normalized.startswith("uz:"):
        return "uz"
    return None


def is_internal_note(text: str) -> bool:
    normalized = text.casefold().strip()
    return normalized.startswith(
        (
            "подсказка оператор",
            "подсказка для оператор",
            "оператору",
            "operatorga",
            "operator uchun",
            "operatorda",
        )
    )


def finalize_entry(entry: dict) -> dict:
    title = entry.pop("title")
    number = entry["id"]
    question_ru, question_uz = clean_question(title, number)
    entry["question_ru"] = question_ru
    entry["question_uz"] = question_uz
    entry["answer_ru"] = "\n\n".join(entry.pop("ru_parts")).strip()
    entry["answer_uz"] = "\n\n".join(entry.pop("uz_parts")).strip()
    entry["internal_notes"] = entry.pop("notes")
    entry["active"] = "неактуально" not in title.casefold()
    return entry


def parse_entries(paragraphs: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    current_language: str | None = None
    expected = 1

    for text in paragraphs:
        number = question_number(text, expected)
        if number is not None:
            if current is not None:
                entries.append(finalize_entry(current))
            current = {
                "id": number,
                "title": text,
                "ru_parts": [],
                "uz_parts": [],
                "notes": [],
            }
            current_language = None
            expected += 1
            continue

        if current is None:
            continue
        marker = language_marker(text)
        if marker:
            current_language = marker
            continue
        if text.startswith("<Image placeholder>"):
            continue
        if is_internal_note(text):
            current["notes"].append(text)
            continue
        if current_language:
            current[f"{current_language}_parts"].append(text)

    if current is not None:
        entries.append(finalize_entry(current))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert the approved FAQ DOCX to runtime JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    entries = parse_entries(read_paragraphs(args.input))
    if [entry["id"] for entry in entries] != list(range(1, 42)):
        raise SystemExit("Expected exactly 41 sequential FAQ entries")
    args.output.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(entries)} FAQ entries to {args.output}")


if __name__ == "__main__":
    main()
