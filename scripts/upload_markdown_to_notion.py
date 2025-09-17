#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upload a local Markdown file under DOCS/ to the "ZOBIS 개발문서 DB" in Notion.

Behavior:
- Loads NOTION_TOKEN and database id from environment variables (.env supported)
- Uses first non-empty line as page title
- Converts Markdown into a small set of Notion blocks (headings, bullets, paragraphs)
- Splits long paragraphs to respect Notion rich_text length limits

Required env vars (first found will be used):
- NOTION_TOKEN
- NOTION_DEV_DOCS_DATABASE_ID | ZOBIS_DEV_DOCS_DB_ID | NOTION_DOCUMENTS_DB_ID
- Optional: NOTION_DEV_DOCS_TITLE_PROP (default: "문서 제목")
"""

import os
import sys
from typing import List, Dict

from notion_client import Client


MAX_RICH_TEXT = 1800  # conservative chunk size


def chunk_text(text: str, chunk_size: int = MAX_RICH_TEXT) -> List[str]:
    text = text or ""
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)] or [""]


def md_line_to_block(line: str) -> Dict:
    line = line.rstrip("\n")
    if not line.strip():
        return {
            "type": "paragraph",
            "paragraph": {"rich_text": []}
        }
    if line.startswith("### "):
        content = line[4:]
        return {"type": "heading_3", "heading_3": {"rich_text": [{"type": "text", "text": {"content": content}}]}}
    if line.startswith("## "):
        content = line[3:]
        return {"type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": content}}]}}
    if line.startswith("# "):
        content = line[2:]
        return {"type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": content}}]}}
    if line.startswith("- "):
        content = line[2:]
        return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": content}}]}}

    # default paragraph, split if needed
    chunks = chunk_text(line)
    if len(chunks) == 1:
        return {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunks[0]}}]}}
    # when too long, create a paragraph with first chunk; the rest will be added by caller
    return {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunks[0]}}]}}


def md_to_blocks(markdown: str) -> List[Dict]:
    blocks: List[Dict] = []
    lines = markdown.splitlines()
    for line in lines:
        block = md_line_to_block(line)
        blocks.append(block)
        # handle overflow for long paragraphs beyond first chunk
        if block["type"] == "paragraph":
            rt = block["paragraph"].get("rich_text", [])
            if rt:
                original = line.rstrip("\n")
                chunks = chunk_text(original)
                for extra in chunks[1:]:
                    blocks.append({
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": extra}}]}
                    })
    return blocks


def extract_title_and_body(md_text: str) -> (str, str):
    lines = md_text.splitlines()
    title = ""
    body_start = 0
    for idx, line in enumerate(lines):
        if line.strip():
            title = line.lstrip("# ").strip()
            body_start = idx + 1
            break
    body = "\n".join(lines[body_start:])
    return title or "무제 문서", body


def get_env_first(*keys: str) -> str:
    for k in keys:
        val = os.getenv(k)
        if val:
            return val
    return ""


def tolerant_load_env(path: str = ".env") -> None:
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for raw in f.readlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        # fail silently; we'll rely on process env if present
        pass


def get_title_property_name(notion: Client, database_id: str, override: str = "") -> str:
    if override:
        return override
    try:
        db = notion.databases.retrieve(database_id=database_id)
        props = db.get("properties", {})
        for name, meta in props.items():
            if meta.get("type") == "title":
                return name
    except Exception:
        pass
    return "문서 제목"


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/upload_markdown_to_notion.py <path-to-markdown>")
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        sys.exit(1)

    tolerant_load_env()
    token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    database_id = get_env_first(
        "NOTION_DEV_DOCS_DATABASE_ID",
        "ZOBIS_DEV_DOCS_DB_ID",
        "NOTION_DOCUMENTS_DB_ID",
    )
    # Initialize client early for title prop auto-detection
    notion = Client(auth=token) if token else None
    title_prop = get_title_property_name(notion, database_id, os.getenv("NOTION_DEV_DOCS_TITLE_PROP", "")) if notion and database_id else os.getenv("NOTION_DEV_DOCS_TITLE_PROP", "문서 제목")

    if not token:
        print("❌ NOTION_TOKEN missing in environment")
        sys.exit(1)
    if not database_id:
        print("❌ Database ID missing. Set one of NOTION_DEV_DOCS_DATABASE_ID / ZOBIS_DEV_DOCS_DB_ID / NOTION_DOCUMENTS_DB_ID in .env")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        md_text = f.read()

    title, body = extract_title_and_body(md_text)
    blocks = md_to_blocks(body)

    notion = notion or Client(auth=token)

    properties = {
        title_prop: {
            "title": [{"type": "text", "text": {"content": title}}]
        }
    }

    created = notion.pages.create(parent={"database_id": database_id}, properties=properties, children=blocks)
    url = created.get("url")
    page_id = created.get("id")
    print(f"✅ Created page: {title}")
    if url:
        print(url)
    else:
        print(page_id)


if __name__ == "__main__":
    main()


