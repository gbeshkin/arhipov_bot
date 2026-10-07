#!/usr/bin/env python3
"""Terminal RAG agent for the @machineintheshell archive.

No third-party packages are required. The agent uses SQLite FTS5 for retrieval
and can optionally send retrieved context to any OpenAI-compatible chat API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "archive.db"
CHUNK_SIZE = 1_400
MAX_RESULTS = 5
STOP_WORDS = {
    "что", "как", "где", "когда", "зачем", "почему", "какой", "какие", "какая",
    "расскажи", "покажи", "найди", "про", "для", "это", "эти", "или", "в", "на",
    "по", "из", "за", "от", "с", "и", "а", "но",
    "the", "about", "what", "does", "say", "tell", "show", "find", "channel",
    "which", "who", "where", "when", "why", "how", "are", "is", "was", "were",
    "and", "or", "for", "from", "with", "into", "that", "this", "these", "those",
    "to", "of", "in", "on", "at", "by", "an", "a",
}


@dataclass
class Source:
    source_id: str
    title: str
    content: str
    published_at: str | None
    url: str | None


def style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def info(text: str) -> None:
    print(style(text, "36"))


def error(text: str) -> None:
    print(style(text, "31"), file=sys.stderr)


def clean_text(value: Any) -> str:
    """Normalize Telegram's string or rich-text-array export format."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                pieces.append(str(item.get("text", "")))
        return "".join(pieces).strip()
    return ""


def make_title(text: str, fallback: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), fallback)
    return textwrap.shorten(line, width=94, placeholder="…")


def split_chunks(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= CHUNK_SIZE:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        if len(current) + len(paragraph) + 2 <= CHUNK_SIZE:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > CHUNK_SIZE:
            cut = paragraph.rfind(" ", 0, CHUNK_SIZE)
            cut = cut if cut > CHUNK_SIZE // 2 else CHUNK_SIZE
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


class Archive:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    source_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    published_at TEXT,
                    url TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
                    title, content, source_id UNINDEXED
                );
                """
            )

    def upsert(self, source: Source) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM document_search WHERE source_id = ?", (source.source_id,))
            con.execute(
                """
                INSERT INTO documents(source_id, title, content, published_at, url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  title = excluded.title,
                  content = excluded.content,
                  published_at = excluded.published_at,
                  url = excluded.url
                """,
                (source.source_id, source.title, source.content, source.published_at, source.url),
            )
            con.execute(
                "INSERT INTO document_search(title, content, source_id) VALUES (?, ?, ?)",
                (source.title, source.content, source.source_id),
            )

    def search(self, question: str, limit: int = MAX_RESULTS) -> list[Source]:
        terms = [
            term for term in re.findall(r"[\w-]{2,}", question.lower(), flags=re.UNICODE)
            if term not in STOP_WORDS
        ]
        if not terms:
            return []
        # OR deliberately favours recall: Russian word forms vary substantially,
        # while BM25 keeps documents matching several terms above single-term hits.
        match = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        with self.connect() as con:
            try:
                rows = con.execute(
                    """
                    SELECT d.*, bm25(document_search, 2.0, 1.0) AS rank
                    FROM document_search
                    JOIN documents d ON d.source_id = document_search.source_id
                    WHERE document_search MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = f"%{terms[0]}%"
                rows = con.execute(
                    """
                    SELECT * FROM documents
                    WHERE lower(title) LIKE ? OR lower(content) LIKE ?
                    ORDER BY published_at DESC
                    LIMIT ?
                    """,
                    (like, like, limit),
                ).fetchall()
        return [
            Source(
                source_id=row["source_id"],
                title=row["title"],
                content=row["content"],
                published_at=row["published_at"],
                url=row["url"],
            )
            for row in rows
        ]

    def stats(self) -> tuple[int, str | None]:
        with self.connect() as con:
            row = con.execute("SELECT count(*) AS total, max(published_at) AS newest FROM documents").fetchone()
        return int(row["total"]), row["newest"]


class TelegramPreviewParser(HTMLParser):
    """Extract readable post text from a public t.me/s/<channel> page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[dict[str, str | None]] = []
        self.current: dict[str, str | None] | None = None
        self.div_depth = 0
        self.capture_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        classes = data.get("class", "") or ""
        post_name = data.get("data-post")
        if tag == "div" and post_name and "tgme_widget_message" in classes:
            self.current = {"post": post_name, "text": "", "url": None, "date": None}
            self.div_depth = 1
            self.capture_tags = []
            return
        if not self.current:
            return
        if tag == "div":
            self.div_depth += 1
        if "tgme_widget_message_text" in classes or "tgme_widget_message_link_preview" in classes:
            self.capture_tags.append(tag)
        if "tgme_widget_message_date" in classes and data.get("href"):
            self.current["url"] = data["href"]
        if tag == "time" and data.get("datetime"):
            self.current["date"] = data["datetime"]

    def handle_data(self, data: str) -> None:
        if self.current and self.capture_tags:
            self.current["text"] = f"{self.current['text']} {data}"

    def handle_endtag(self, tag: str) -> None:
        if not self.current:
            return
        if self.capture_tags and tag == self.capture_tags[-1]:
            self.capture_tags.pop()
        if tag == "div":
            self.div_depth -= 1
            if self.div_depth == 0:
                self.current["text"] = re.sub(r"\s+", " ", str(self.current["text"] or "")).strip()
                if self.current["text"]:
                    self.posts.append(self.current)
                self.current = None
                self.capture_tags = []


def fetch_telegram_page(channel: str, before: int | None = None) -> list[dict[str, str | None]]:
    url = f"https://t.me/s/{channel}"
    if before is not None:
        url += f"?before={before}"
    request = urllib.request.Request(url, headers={"User-Agent": "MachineInTheShellArchive/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Could not download {url}: {exc}") from exc
    except urllib.error.URLError as urllib_exc:
        # Some Python distributions lack the locally installed corporate CA,
        # while the OS curl trust store is configured correctly. Curl still
        # validates TLS by default; this is not an insecure retry.
        try:
            result = subprocess.run(
                ["curl", "--fail", "--silent", "--show-error", "--location", url],
                capture_output=True,
                check=True,
                timeout=35,
            )
            body = result.stdout.decode("utf-8", errors="replace")
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as curl_exc:
            raise RuntimeError(f"Could not download {url}: {urllib_exc}; curl fallback failed: {curl_exc}") from curl_exc
    parser = TelegramPreviewParser()
    parser.feed(body)
    return parser.posts


def sync_telegram_channel(archive: Archive, channel: str, pages: int) -> tuple[int, int, int]:
    channel = channel.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,}", channel):
        raise ValueError("Channel must be a public Telegram username, e.g. machineintheshell")
    before: int | None = None
    seen_oldest: set[int] = set()
    post_count = chunk_count = completed_pages = 0
    for page_number in range(pages):
        posts = fetch_telegram_page(channel, before)
        if not posts:
            break
        post_ids: list[int] = []
        for post in posts:
            post_path = str(post["post"])
            try:
                post_id = int(post_path.rsplit("/", 1)[1])
            except (IndexError, ValueError):
                continue
            post_ids.append(post_id)
            content = str(post["text"])
            chunks = split_chunks(content)
            title = make_title(content, f"Telegram post {post_id}")
            for chunk_index, chunk in enumerate(chunks, start=1):
                suffix = f":{chunk_index}" if len(chunks) > 1 else ""
                archive.upsert(
                    Source(
                        source_id=f"telegram:{channel}:{post_id}{suffix}",
                        title=title,
                        content=chunk,
                        published_at=post["date"],
                        url=post["url"] or f"https://t.me/{channel}/{post_id}",
                    )
                )
                chunk_count += 1
            post_count += 1
        completed_pages += 1
        if not post_ids:
            break
        oldest = min(post_ids)
        if oldest in seen_oldest:
            break
        seen_oldest.add(oldest)
        before = oldest
        if page_number + 1 < pages:
            time.sleep(0.35)
    return completed_pages, post_count, chunk_count


def telegram_records(path: Path) -> Iterable[Source]:
    """Read a Telegram Desktop JSON export or a simple JSON/JSONL post list."""
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw) if path.suffix == ".json" else [json.loads(line) for line in raw.splitlines() if line.strip()]
    messages = payload.get("messages", []) if isinstance(payload, dict) else payload
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = clean_text(message.get("text") or message.get("content"))
        if not content:
            continue
        post_id = str(message.get("id") or message.get("message_id") or index)
        date = str(message.get("date") or message.get("published_at") or "") or None
        url = message.get("url") or message.get("link")
        title = str(message.get("title") or make_title(content, f"Пост {post_id}"))
        chunks = split_chunks(content)
        for chunk_index, chunk in enumerate(chunks, start=1):
            suffix = f":{chunk_index}" if len(chunks) > 1 else ""
            yield Source(f"{post_id}{suffix}", title, chunk, date, str(url) if url else None)


def markdown_records(path: Path) -> Iterable[Source]:
    body = path.read_text(encoding="utf-8")
    for index, post in enumerate(re.split(r"\n---+\n", body), start=1):
        content = post.strip()
        if not content:
            continue
        title = make_title(content, f"Заметка {index}")
        for chunk_index, chunk in enumerate(split_chunks(content), start=1):
            yield Source(f"{path.stem}:{index}:{chunk_index}", title, chunk, None, None)


def ingest(archive: Archive, input_path: Path) -> int:
    records = telegram_records(input_path) if input_path.suffix in {".json", ".jsonl"} else markdown_records(input_path)
    count = 0
    for record in records:
        archive.upsert(record)
        count += 1
    return count


def build_context(sources: list[Source]) -> str:
    entries = []
    for index, source in enumerate(sources, start=1):
        entries.append(
            f"[{index}] {source.title}\nДата: {source.published_at or 'не указана'}\n{source.content}"
        )
    return "\n\n".join(entries)


def ask_llm(question: str, sources: list[Source]) -> str | None:
    """Use an OpenAI-compatible /chat/completions endpoint when configured."""
    url = os.getenv("AGENT_LLM_URL")
    api_key = os.getenv("AGENT_LLM_KEY")
    model = os.getenv("AGENT_LLM_MODEL")
    if not all((url, api_key, model)):
        return None
    prompt = (
        "You answer questions about a Telegram channel archive. Answer in English and use only the "
        "context below. Do not add outside facts. Say when the archive does not support an answer. "
        "Cite source fragments after claims in the form [1].\n\nContext:\n" + build_context(sources)
    )
    payload = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "You are a precise research assistant."},
                {"role": "user", "content": f"Question: {question}\n\n{prompt}"},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"]).strip()
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
        error(f"LLM unavailable ({exc}). Showing retrieved archive material instead.")
        return None


def local_answer(question: str, sources: list[Source]) -> str:
    if not sources:
        return "No archive posts confidently answer that question yet. Try different keywords or sync the channel first."
    lines = [f"Found {len(sources)} relevant archive fragment(s) for: {question}"]
    for index, source in enumerate(sources, start=1):
        excerpt = textwrap.shorten(re.sub(r"\s+", " ", source.content), width=260, placeholder="…")
        lines.append(f"{index}. {excerpt} [{index}]")
    return "\n\n".join(lines)


def print_sources(sources: list[Source]) -> None:
    if not sources:
        return
    print("\n" + style("SOURCES", "2;37"))
    for index, source in enumerate(sources, start=1):
        meta = " · ".join(value for value in (source.published_at, source.url) if value)
        print(f"[{index}] {style(source.title, '1')}" + (f"\n    {meta}" if meta else ""))


def run_question(archive: Archive, question: str) -> list[Source]:
    sources = archive.search(question)
    answer = ask_llm(question, sources) if sources else None
    print("\n" + style("MACHINE IN THE SHELL / ARCHIVE ANSWER", "1;36"))
    print(answer or local_answer(question, sources))
    print_sources(sources)
    return sources


def chat(archive: Archive) -> None:
    total, newest = archive.stats()
    print(style("Machine in the Shell / terminal agent", "1;36"))
    print(f"Archive fragments: {total}." + (f" Latest post: {newest}." if newest else ""))
    print("Ask a question. Commands: /help, /stats, /exit\n")
    while True:
        try:
            question = input(style("› ", "1;33")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return
        if question in {"/exit", "/quit", "/q"}:
            return
        if question == "/help":
            print("Ask a question in plain text. /stats shows archive size; /exit quits.")
            continue
        if question == "/stats":
            total, newest = archive.stats()
            print(f"Archive fragments: {total}; latest post: {newest or 'no data'}.")
            continue
        if question:
            run_question(archive, question)


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description="Terminal agent for the @machineintheshell archive")
    command_parser.add_argument("--database", type=Path, default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    commands = command_parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the local database")
    ingest_parser = commands.add_parser("ingest", help="import Telegram JSON/JSONL or Markdown")
    ingest_parser.add_argument("file", type=Path)
    sync_parser = commands.add_parser("sync-channel", help="download posts from a public Telegram channel")
    sync_parser.add_argument("--channel", default="machineintheshell", help="public channel username (default: machineintheshell)")
    sync_parser.add_argument("--pages", type=int, default=50, help="number of 20-post pages to fetch (default: 50)")
    ask_parser = commands.add_parser("ask", help="ask one question")
    ask_parser.add_argument("question", nargs="+")
    commands.add_parser("chat", help="start interactive terminal mode")
    commands.add_parser("stats", help="show archive status")
    return command_parser


def main() -> int:
    args = parser().parse_args()
    archive = Archive(args.database)
    archive.initialize()
    if args.command == "init":
        info(f"Database ready: {args.database}")
    elif args.command == "ingest":
        if not args.file.exists():
            error(f"File not found: {args.file}")
            return 2
        count = ingest(archive, args.file)
        info(f"Imported fragments: {count}")
    elif args.command == "sync-channel":
        if args.pages < 1:
            error("--pages must be at least 1")
            return 2
        try:
            completed_pages, posts, chunks = sync_telegram_channel(archive, args.channel, args.pages)
        except (RuntimeError, ValueError) as exc:
            error(str(exc))
            return 1
        info(f"Synced @{args.channel}: {posts} posts, {chunks} fragments across {completed_pages} page(s).")
    elif args.command == "ask":
        run_question(archive, " ".join(args.question))
    elif args.command == "chat":
        chat(archive)
    elif args.command == "stats":
        total, newest = archive.stats()
        print(f"Fragments: {total}\nLatest post: {newest or 'no data'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
