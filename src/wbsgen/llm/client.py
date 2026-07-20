"""LLM client with mock mode for development/testing."""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

        # Log
        log = {
            "run_id": run_id,
            "model": self.model if not self.mock else "mock",
            "mock": self.mock,
            "temperature": temperature,
            "input_hash": hashlib.sha256((system + user).encode()).hexdigest()[:16],
            "input_length": len(system) + len(user),
            "output": result,
            "elapsed_seconds": elapsed,
            "timestamp": start.isoformat(),
        }
        log_path = self.log_dir / f"{run_id}.json"
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

        return result

    def _mock_generate(self, system: str, user: str, schema: dict | None) -> dict:
        """Generate mock response based on schema."""
        if schema is None:
            return {"result": "mock"}

        return _generate_from_schema(schema, user)

    def _real_generate(
        self, system: str, user: str, schema: dict | None, temperature: float
    ) -> dict:
        """Call real LLM endpoint (not used in dev)."""
        from openai import OpenAI
        client = OpenAI(
            base_url=self.cfg["base_url"],
            api_key=self.cfg["api_key"],
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for attempt in range(self.max_retries):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                text = resp.choices[0].message.content
                return json.loads(text)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"LLM failed after {self.max_retries} retries: {e}")
        return {}


def _generate_from_schema(schema: dict, context: str = "") -> dict:
    """Generate synthetic data matching a JSON schema."""
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
    """Generate a value for a schema property."""
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
