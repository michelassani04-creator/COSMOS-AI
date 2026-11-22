"""Mémoire éditoriale (SQLite) : brouillons, statuts et détection des doublons."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

from .models import Draft

# Un sujet rejeté par l'humain peut être retraité plus tard.
_IGNORED_FOR_DUPLICATES = ("rejected",)
_EXTRA_COLUMNS = ("sources", "review")  # ajoutées en phase 3


def topic_tokens(text: str) -> set[str]:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return {t for t in re.findall(r"[a-z0-9]+", plain) if len(t) > 2}


def similarity(a: str, b: str) -> float:
    ta, tb = topic_tokens(a), topic_tokens(b)
    if not ta and not tb:
        return 1.0 if a.strip().lower() == b.strip().lower() else 0.0
    return len(ta & tb) / len(ta | tb)


class EditorialMemory:
    def __init__(self, path: str | Path = ":memory:", threshold: float = 0.8) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._threshold = threshold
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                script TEXT NOT NULL,
                caption TEXT NOT NULL,
                video_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                post_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sources TEXT NOT NULL DEFAULT '',
                review TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Ajoute les colonnes manquantes d'une base créée par une version plus ancienne."""
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(drafts)")}
        for column in _EXTRA_COLUMNS:
            if column not in existing:
                self._db.execute(f"ALTER TABLE drafts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _to_draft(row: sqlite3.Row) -> Draft:
        return Draft(
            id=row["id"],
            topic=row["topic"],
            script=row["script"],
            caption=row["caption"],
            video_path=row["video_path"],
            sources=row["sources"],
            review=row["review"],
            status=row["status"],
            post_id=row["post_id"],
        )

    def save_draft(self, draft: Draft, status: str) -> Draft:
        cur = self._db.execute(
            "INSERT INTO drafts (topic, script, caption, video_path, sources, review, status)"
            " VALUES (?,?,?,?,?,?,?)",
            (draft.topic, draft.script, draft.caption, draft.video_path,
             draft.sources, draft.review, status),
        )
        self._db.commit()
        saved = self.get_draft(cur.lastrowid)
        assert saved is not None
        return saved

    def get_draft(self, draft_id: int) -> Draft | None:
        row = self._db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return self._to_draft(row) if row else None

    def set_status(self, draft_id: int, status: str, post_id: str | None = None) -> None:
        self._db.execute(
            "UPDATE drafts SET status = ?, post_id = COALESCE(?, post_id) WHERE id = ?",
            (status, post_id, draft_id),
        )
        self._db.commit()

    def list_drafts(self, status: str | None = None) -> list[Draft]:
        if status is None:
            rows = self._db.execute("SELECT * FROM drafts ORDER BY id").fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM drafts WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        return [self._to_draft(r) for r in rows]

    def is_duplicate(self, topic: str, threshold: float | None = None) -> bool:
        limit = self._threshold if threshold is None else threshold
        marks = ",".join("?" for _ in _IGNORED_FOR_DUPLICATES)
        rows = self._db.execute(
            f"SELECT topic FROM drafts WHERE status NOT IN ({marks})", _IGNORED_FOR_DUPLICATES
        ).fetchall()
        return any(similarity(topic, r["topic"]) >= limit for r in rows)

    def is_source_used(self, url: str) -> bool:
        """Une actualité (identifiée par son adresse) a-t-elle déjà donné un brouillon ?"""
        if not url:
            return False
        marks = ",".join("?" for _ in _IGNORED_FOR_DUPLICATES)
        rows = self._db.execute(
            f"SELECT sources FROM drafts WHERE status NOT IN ({marks})", _IGNORED_FOR_DUPLICATES
        ).fetchall()
        return any(url in row["sources"] for row in rows)

    def close(self) -> None:
        self._db.close()
