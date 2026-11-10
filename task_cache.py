"""Small, local retrieval cache for verified desktop workflows.

The cache is deliberately advisory: it returns a workflow hint for the LLM and
never executes cached actions by itself. SQLite is used so the agent has no new
runtime dependency and remains usable as a single-user Linux process.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from config import SCREEN_MUTATING_TOOLS


def normalize_text(value: str) -> str:
    """Normalize Vietnamese/user text for stable cache lookup."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_verified_workflow(steps: list[dict[str, Any]]) -> bool:
    """No step failed, and a screenshot came AFTER the last action that changed
    the screen. A screenshot taken before sending proves nothing about the send."""
    if any(step["result"].startswith("error") for step in steps):
        return False
    last_action = max(
        (i for i, step in enumerate(steps) if step["tool"] in SCREEN_MUTATING_TOOLS),
        default=-1,
    )
    return any(step["tool"] == "screenshot" for step in steps[last_action + 1:])


# A tap's target/keywords/anchor hold names of people. A hint carrying them can
# steer a request for "Tường" to a workflow recorded for "Trường".
_NAME_FREE_TAP_ARGS = ("area", "relation", "control")


def _name_free(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name != "tap":
        return args
    return {key: args[key] for key in _NAME_FREE_TAP_ARGS if key in args}


def _safe_step(name: str, args: dict[str, Any], result: str) -> dict[str, Any]:
    """Keep replay hints useful without persisting typed secrets/messages."""
    saved_args = dict(_name_free(name, args))
    if name == "type":
        saved_args["text"] = "{{text_from_current_request}}"
    elif "text" in saved_args and isinstance(saved_args["text"], str):
        saved_args["text"] = "{{runtime_text}}"
    return {
        "tool": name,
        "arguments": saved_args,
        "result": (result or "")[:240],
    }


class TaskCache:
    """SQLite-backed retrieval cache for verified task workflows."""

    def __init__(
        self,
        path: str | os.PathLike[str] = "agent_cache.sqlite3",
        *,
        enabled: bool = True,
        threshold: float = 0.82,
        max_age_days: int = 30,
    ) -> None:
        self.enabled = enabled
        self.threshold = threshold
        self.max_age_seconds = max_age_days * 24 * 60 * 60
        self.path = Path(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            # The first table had no device_id, so its rows cannot be scoped.
            connection.execute("DROP TABLE IF EXISTS task_workflows")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_workflows_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL,
                    skills TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    UNIQUE(device_id, intent, skills)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_workflows_v2_verified "
                "ON task_workflows_v2(verified, last_used_at)"
            )

    def retrieve(self, intent: str, skills: list[str],
                 device_id: str = "") -> dict[str, Any] | None:
        """Return the device's best recent verified workflow, or ``None``."""
        if not self.enabled:
            return None
        normalized = normalize_text(intent)
        if not normalized:
            return None
        cutoff = time.time() - self.max_age_seconds
        skill_key = ",".join(sorted(skills))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_workflows_v2 "
                "WHERE verified = 1 AND last_used_at >= ? AND device_id = ?",
                (cutoff, device_id),
            ).fetchall()

            best_row = None
            best_score = 0.0
            unverified_ids = []
            for row in rows:
                if not is_verified_workflow(json.loads(row["workflow"])):
                    unverified_ids.append((row["id"],))   # saved under the old rule
                    continue
                text_score = SequenceMatcher(
                    None, normalized, row["intent"]
                ).ratio()
                skill_score = 1.0 if row["skills"] == skill_key else 0.0
                score = text_score * 0.85 + skill_score * 0.15
                if score > best_score:
                    best_score = score
                    best_row = row

            connection.executemany("DELETE FROM task_workflows_v2 WHERE id = ?", unverified_ids)

            if best_row is None or best_score < self.threshold:
                return None

            connection.execute(
                "UPDATE task_workflows_v2 SET usage_count = usage_count + 1, "
                "last_used_at = ? WHERE id = ?",
                (time.time(), best_row["id"]),
            )
            return {
                "id": best_row["id"],
                "score": round(best_score, 3),
                "intent": best_row["intent"],
                "workflow": json.loads(best_row["workflow"]),
            }

    def record_verified(
        self,
        intent: str,
        skills: list[str],
        steps: list[dict[str, Any]],
        device_id: str = "",
    ) -> None:
        """Store a successful workflow only when the caller already verified it."""
        if not self.enabled or not steps:
            return
        normalized = normalize_text(intent)
        if not normalized:
            return
        now = time.time()
        skill_key = ",".join(sorted(skills))
        workflow = [
            _safe_step(step["tool"], step["arguments"], step["result"])
            for step in steps
        ]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_workflows_v2
                    (device_id, intent, skills, workflow, verified, usage_count,
                     created_at, last_used_at)
                VALUES (?, ?, ?, ?, 1, 0, ?, ?)
                ON CONFLICT(device_id, intent, skills) DO UPDATE SET
                    workflow = excluded.workflow,
                    verified = 1,
                    last_used_at = excluded.last_used_at
                """,
                (
                    device_id,
                    normalized,
                    skill_key,
                    json.dumps(workflow, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    @staticmethod
    def prompt_hint(match: dict[str, Any]) -> str:
        """Convert a cache hit into an explicit, non-authoritative LLM hint."""
        steps = []
        for index, step in enumerate(match["workflow"], start=1):
            steps.append(
                f"{index}. {step['tool']} "
                # Filtered here too: rows saved by older versions still have names.
                f"{json.dumps(_name_free(step['tool'], step['arguments']), ensure_ascii=False)}"
            )
        return (
            "Có một workflow cũ tương tự trong cache (độ tương đồng "
            f"{match['score']}). Đây chỉ là gợi ý tham khảo, KHÔNG được replay "
            "mù; hãy kiểm tra screenshot/context và xác minh từng bước. Tên "
            "người và nội dung phải lấy từ yêu cầu HIỆN TẠI.\n"
            + "\n".join(steps)
        )
