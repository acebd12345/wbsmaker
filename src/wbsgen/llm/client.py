"""LLM client with mock mode, chunked calls, and 504 retry/split."""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default safe chunk size (characters). Kept conservative to avoid 504.
DEFAULT_CHUNK_SIZE = 2500
MIN_CHUNK_SIZE = 500


class LLMClient:
    def __init__(self, cfg: dict, proj_dir: Path):
        self.cfg = cfg.get("llm", {})
        self.mock = self.cfg.get("mock", True)
        self.model = self.cfg.get("model", "gemma-4-31b-it")
        self.max_retries = self.cfg.get("max_retries", 3)
        self.log_dir = proj_dir / "llm_logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def generate_json(
        self,
        system: str,
        user: str,
        schema: dict | None = None,
        temperature: float = 0.0,
    ) -> dict:
        """Generate JSON response. Uses mock in development."""
        run_id = str(uuid.uuid4())[:8]
        start = datetime.now(timezone.utc)

        if self.mock:
            result = self._mock_generate(system, user, schema)
        else:
            result = self._real_generate(system, user, schema, temperature)

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        log = {
            "run_id": run_id,
            "model": self.model if not self.mock else "mock",
            "mock": self.mock,
            "temperature": temperature,
            "input_hash": hashlib.sha256((system + user).encode()).hexdigest()[:16],
            "input_length": len(user),
            "output": result,
            "elapsed_seconds": elapsed,
            "timestamp": start.isoformat(),
        }
        log_path = self.log_dir / f"{run_id}.json"
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

        return result

    def _mock_generate(self, system: str, user: str, schema: dict | None) -> dict:
        if schema is None:
            return {"result": "mock"}
        return _generate_from_schema(schema, user)

    def _real_generate(
        self, system: str, user: str, schema: dict | None, temperature: float
    ) -> dict:
        """Call real LLM endpoint with retry + exponential backoff."""
        from openai import OpenAI
        client = OpenAI(
            base_url=self.cfg["base_url"],
            api_key=self.cfg.get("api_key", "not-needed"),
            timeout=120.0,
            max_retries=0,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    timeout=120.0,
                )
                text = resp.choices[0].message.content
                return json.loads(text)
            except Exception as e:
                last_err = e
                import time as _time
                _time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"LLM failed after {self.max_retries} retries: {last_err}")


def chunk_text(text: str, max_chars: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """Split text into chunks at paragraph/section boundaries.

    Never truncates mid-sentence. Each chunk is a complete unit.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    # Split on double newlines (paragraphs) or markdown headings
    segments = re.split(r'\n(?=\n|#{1,4}\s|表\s*\d+|\|)', text)

    current = ""
    for seg in segments:
        if not seg.strip():
            continue
        if len(current) + len(seg) + 1 > max_chars and current.strip():
            chunks.append(current.strip())
            current = seg
        else:
            current = current + "\n" + seg if current else seg

    if current.strip():
        chunks.append(current.strip())

    # If any chunk is still too large, split on single newlines
    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
        else:
            lines = c.split("\n")
            buf = ""
            for line in lines:
                if len(buf) + len(line) + 1 > max_chars and buf.strip():
                    final.append(buf.strip())
                    buf = line
                else:
                    buf = buf + "\n" + line if buf else line
            if buf.strip():
                final.append(buf.strip())

    return final if final else [text[:max_chars]]


def chunk_table_rows(
    header: list[str], rows: list[list[str]], caption: str = "",
    max_chars: int = DEFAULT_CHUNK_SIZE
) -> list[str]:
    """Split table rows into multiple markdown chunks, each with header.

    Each chunk is a valid markdown table with the same header.
    """
    if not header or not rows:
        return []

    header_md = "| " + " | ".join(h or "" for h in header) + " |\n"
    header_md += "| " + " | ".join("---" for _ in header) + " |\n"
    prefix = f"{caption}\n{header_md}" if caption else header_md

    chunks: list[str] = []
    current = prefix
    for row in rows:
        padded = list(row) + [""] * (len(header) - len(row))
        row_md = "| " + " | ".join(c or "" for c in padded[:len(header)]) + " |\n"
        if len(current) + len(row_md) > max_chars and current != prefix:
            chunks.append(current.strip())
            current = prefix + row_md
        else:
            current += row_md

    if current.strip() and current != prefix.strip():
        chunks.append(current.strip())

    return chunks if chunks else [prefix.strip()]


def _generate_from_schema(schema: dict, context: str = "") -> dict:
    schema_type = schema.get("type", "object")
    if schema_type == "object":
        result = {}
        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            result[key] = _generate_value(prop_schema, key, context)
        return result
    if schema_type == "array":
        items_schema = schema.get("items", {})
        return [_generate_value(items_schema, "item", context)]
    return _generate_value(schema, "root", context)


def _generate_value(schema: dict, key: str, context: str) -> Any:
    t = schema.get("type", "string")
    if t == "string":
        if "enum" in schema:
            return schema["enum"][0]
        return f"mock_{key}"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "array":
        items = schema.get("items", {"type": "string"})
        return [_generate_value(items, f"{key}_item", context)]
    if t == "object":
        return _generate_from_schema(schema, context)
    return None
