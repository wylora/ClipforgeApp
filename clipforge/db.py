"""SQLite: проекты, клипы, обратная связь, ключ-значение."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import app_home
from .feedback import LABELS, group_for_mode, labels_for_group

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, title TEXT, created REAL, status TEXT, mode TEXT,
  settings_json TEXT, work_dir TEXT, out_dir TEXT, video_path TEXT, duration REAL, language TEXT, message TEXT,
  report_path TEXT, cand_key TEXT);
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, idx INTEGER, start REAL, "end" REAL, anchor REAL,
  mode TEXT, title TEXT, titles_json TEXT, description TEXT, hashtags TEXT, scores_json TEXT, reasons_json TEXT,
  details_json TEXT, text TEXT, file_path TEXT, thumb_path TEXT, srt_path TEXT, subtitles_json TEXT,
  overrides_json TEXT, status TEXT, error TEXT, render_hash TEXT, favorite INTEGER DEFAULT 0, label TEXT,
  created REAL, FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER, project_id INTEGER, mode TEXT, label TEXT,
  scores_json TEXT, created REAL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""
JSON_COLS = {"titles_json", "scores_json", "reasons_json", "details_json", "subtitles_json", "overrides_json", "settings_json"}


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or (app_home() / "clipforge.db"))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _run(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(dict(r)) for r in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        for k in list(row):
            if k in JSON_COLS:
                v = row[k]
                try:
                    row[k[:-5]] = json.loads(v) if v else None
                except ValueError:
                    row[k[:-5]] = None
        return row

    @staticmethod
    def _encode(fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            if k + "_json" in JSON_COLS:
                out[k + "_json"] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                out[k] = v
        return out

    # ------------------------------------------------------------ проекты
    def add_project(self, source: str, title: str, mode: str, settings: dict, out_dir: str = "") -> int:
        cur = self._run("INSERT INTO projects(source,title,created,status,mode,settings_json,out_dir) VALUES(?,?,?,?,?,?,?)",
                        (source, title, time.time(), "queued", mode, json.dumps(settings, ensure_ascii=False), out_dir))
        return int(cur.lastrowid)

    def update_project(self, pid: int, **fields) -> None:
        f = self._encode(fields)
        if f:
            self._run(f"UPDATE projects SET {','.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), pid))

    def get_project(self, pid: int) -> dict | None:
        r = self._all("SELECT * FROM projects WHERE id=?", (pid,))
        return r[0] if r else None

    def list_projects(self) -> list[dict]:
        return self._all("SELECT * FROM projects ORDER BY created DESC")

    def delete_project(self, pid: int) -> None:
        self._run("DELETE FROM clips WHERE project_id=?", (pid,))
        self._run("DELETE FROM feedback WHERE project_id=?", (pid,))
        self._run("DELETE FROM projects WHERE id=?", (pid,))

    # ------------------------------------------------------------ клипы
    def add_clip(self, project_id: int, idx: int, **fields) -> int:
        f = self._encode({"project_id": project_id, "idx": idx, "created": time.time(), "status": "pending", **fields})
        if "end_" in f:
            f["end"] = f.pop("end_")
        cols = ",".join(f'"{k}"' for k in f)
        cur = self._run(f"INSERT INTO clips({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))
        return int(cur.lastrowid)

    def update_clip(self, cid: int, **fields) -> None:
        f = self._encode(fields)
        if "end_" in f:
            f["end"] = f.pop("end_")
        if f:
            self._run(f"UPDATE clips SET {','.join(chr(34) + k + chr(34) + '=?' for k in f)} WHERE id=?", (*f.values(), cid))

    def get_clip(self, cid: int) -> dict | None:
        r = self._all("SELECT * FROM clips WHERE id=?", (cid,))
        return r[0] if r else None

    def get_clips(self, project_id: int, include_deleted: bool = False) -> list[dict]:
        q = "SELECT * FROM clips WHERE project_id=?" + ("" if include_deleted else " AND status!='deleted'") + " ORDER BY idx"
        return self._all(q, (project_id,))

    def delete_clips(self, project_id: int) -> None:
        self._run("DELETE FROM clips WHERE project_id=?", (project_id,))

    # ------------------------------------------------------------ обратная связь
    def set_feedback(self, clip: dict, label: str) -> None:
        if label not in LABELS:
            raise ValueError(label)
        self._run("DELETE FROM feedback WHERE clip_id=?", (clip["id"],))
        self._run("INSERT INTO feedback(clip_id,project_id,mode,label,scores_json,created) VALUES(?,?,?,?,?,?)",
                  (clip["id"], clip["project_id"], clip["mode"], label, json.dumps(clip.get("scores") or {}), time.time()))
        self._run("UPDATE clips SET label=? WHERE id=?", (label, clip["id"]))

    def feedback_samples(self) -> dict[str, list[tuple[dict, int]]]:
        out: dict[str, list[tuple[dict, int]]] = {"humor": [], "general": []}
        for r in self._all("SELECT * FROM feedback"):
            group = group_for_mode(r["mode"])
            if r["label"] in labels_for_group(group):
                out[group].append((json.loads(r["scores_json"] or "{}"), LABELS[r["label"]]))
        return out

    # ------------------------------------------------------------ kv
    def kv_get(self, key: str, default=None):
        r = self._all("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r[0]["value"]) if r else default

    def kv_set(self, key: str, value) -> None:
        self._run("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, json.dumps(value)))
