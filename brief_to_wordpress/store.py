from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class MissingRun(Exception):
    pass


class StateConflict(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    destination TEXT NOT NULL,
                    planner TEXT NOT NULL,
                    brief_json TEXT NOT NULL,
                    design_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    blocks TEXT NOT NULL,
                    draft_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sample_drafts (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    blocks TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db

    @staticmethod
    def _event(db, run_id: str, kind: str, detail: str):
        db.execute(
            "INSERT INTO events (run_id, kind, detail, at) VALUES (?, ?, ?, ?)",
            (run_id, kind, detail, now()),
        )

    @staticmethod
    def _row(db, run_id: str):
        row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise MissingRun(run_id)
        return row

    def create(self, brief: dict, design: dict, plan: dict, blocks: str, destination: str, planner: str, events):
        run_id = uuid4().hex
        slug = f"brief-{run_id}"
        snapshot = json.dumps([plan, blocks, destination, slug], sort_keys=True)
        revision = hashlib.sha256(snapshot.encode()).hexdigest()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO runs
                (id, state, revision, slug, destination, planner, brief_json, design_json, plan_json, blocks, created_at)
                VALUES (?, 'review', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, revision, slug, destination, planner, json.dumps(brief), json.dumps(design),
                 json.dumps(plan), blocks, now()),
            )
            for kind, detail in events:
                self._event(db, run_id, kind, detail)
            self._event(db, run_id, "ready_for_review", "The validated page and its revision are ready to review.")
        return self.get(run_id)

    def get(self, run_id: str) -> dict:
        with self.connect() as db:
            value = dict(self._row(db, run_id))
            value["events"] = [
                dict(row)
                for row in db.execute(
                    "SELECT kind, detail, at FROM events WHERE run_id = ? ORDER BY id", (run_id,)
                )
            ]
        for field in ("brief", "design", "plan", "draft"):
            payload = value.pop(f"{field}_json")
            value[field] = json.loads(payload) if payload is not None else None
        return value

    def recent(self) -> list[dict]:
        with self.connect() as db:
            return [
                {
                    "id": row["id"],
                    "state": row["state"],
                    "title": json.loads(row["plan_json"])["title"],
                    "created_at": row["created_at"],
                }
                for row in db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 12")
            ]

    def approve(self, run_id: str, revision: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id)
            if row["revision"] != revision:
                raise StateConflict("The revision changed. Review the current draft.")
            if row["state"] == "review":
                db.execute("UPDATE runs SET state = 'approved' WHERE id = ?", (run_id,))
                self._event(db, run_id, "approved", f"Approved revision {revision[:12]}.")

    def claim_delivery(self, run_id: str, destination: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id)
            if row["destination"] != destination:
                raise StateConflict("The configured WordPress destination differs from the reviewed destination.")
            if row["state"] == "draft_created":
                return False
            if row["state"] != "approved":
                raise StateConflict("Delivery requires approval and can only be started once.")
            db.execute("UPDATE runs SET state = 'sending' WHERE id = ?", (run_id,))
            self._event(db, run_id, "sending", "Started a single draft creation request.")
            return True

    def needs_check(self, run_id: str, detail: str):
        with self.connect() as db:
            result = db.execute(
                "UPDATE runs SET state = 'needs_check' WHERE id = ? AND state = 'sending'", (run_id,)
            )
            if result.rowcount:
                self._event(db, run_id, "needs_check", detail)

    def delivered(self, run_id: str, draft: dict, recovered: bool):
        with self.connect() as db:
            result = db.execute(
                """
                UPDATE runs SET state = 'draft_created', draft_json = ?
                WHERE id = ? AND state IN ('sending', 'needs_check')
                """,
                (json.dumps(draft), run_id),
            )
            if result.rowcount:
                self._event(
                    db, run_id, "draft_created",
                    f"Found existing draft {draft['id']} by its unique slug." if recovered
                    else f"Draft {draft['id']} was confirmed.",
                )

    def note(self, run_id: str, kind: str, detail: str):
        with self.connect() as db:
            self._event(db, run_id, kind, detail)

    def save_sample_draft(self, run: dict) -> dict:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO sample_drafts (run_id, slug, title, blocks) VALUES (?, ?, ?, ?)",
                (run["id"], run["slug"], run["plan"]["title"], run["blocks"]),
            )
            draft_id = cursor.lastrowid
        return {"id": draft_id, "url": f"/api/sample-drafts/{draft_id}"}

    def find_sample_draft(self, run: dict) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id, blocks FROM sample_drafts WHERE slug = ?", (run["slug"],)).fetchone()
        if row is None or row["blocks"] != run["blocks"]:
            return None
        return {"id": row["id"], "url": f"/api/sample-drafts/{row['id']}"}

    def sample_draft(self, draft_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM sample_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "title": row["title"], "content": row["blocks"],
            "slug": row["slug"], "status": "draft",
        }
