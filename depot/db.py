"""The index: one sqlite file that makes lookups fast. It holds scan results, computed fingerprints, what civitai
answered for each fingerprint (all of which can be rebuilt from the model files and the records in store.py), and the
download queue.

All calls are made from the server's event loop thread; worker threads hand their results back to it.
"""
import contextlib
import json
import logging
import os
import sqlite3
import time

log = logging.getLogger("depot.db")

SCHEMA_VERSION = 7

_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
  id INTEGER PRIMARY KEY,
  position REAL NOT NULL,                 -- queue order
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  -- queued | running | verifying | paused | retrying | failed | done
  state TEXT NOT NULL,
  site TEXT NOT NULL,                     -- the civitai site the model was read from
  model_id INTEGER NOT NULL,
  version_id INTEGER NOT NULL,
  file_id INTEGER NOT NULL,
  title TEXT,
  version_name TEXT,
  model_type TEXT,
  comfy_type TEXT,
  family TEXT,
  base_model TEXT,
  file_name TEXT NOT NULL,                -- civitai's name for the file
  dest_dir TEXT NOT NULL,
  dest_name TEXT NOT NULL,                -- the name it is written under (a .part file until it is checked)
  expected_sha256 TEXT,
  expected_bytes INTEGER,
  bytes_total INTEGER,
  bytes_done INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,    -- failed tries in a row, for the waits between retries
  next_try_at REAL,
  error TEXT,
  error_detail TEXT,
  final_path TEXT,
  sha256 TEXT,
  cover_index INTEGER,
  cover_level INTEGER,
  version_json TEXT NOT NULL,             -- the version as the model's record will keep it
  finished_at REAL
)"""

# Changes to an existing index, by the version they lead to. The index holds hours of computed fingerprints on a big
# library, so it is altered in place, never thrown away for a new column.
_RETIRE = (
    # the fingerprints of the versions this download replaces: they go to the recycle bin once it is done and checked
    "ALTER TABLE downloads ADD COLUMN retire TEXT",
    # what became of them: {"moved": [file names], "error": code or null}
    "ALTER TABLE downloads ADD COLUMN retired TEXT",
)

_CARDS = (
    # empty | suggested | confirmed: what the model's card holds (cards.py), for the lists
    "ALTER TABLE models ADD COLUMN card_state TEXT NOT NULL DEFAULT 'empty'",
    # the drafter version (cards.DRAFTER) the card was last drafted with; 0: to be drafted
    "ALTER TABLE models ADD COLUMN card_drafted INTEGER NOT NULL DEFAULT 0",
)

_USER = (
    # the name and the base model the user gave the model (record["user"]), for the lists; NULL: not given
    "ALTER TABLE models ADD COLUMN user_title TEXT",
    "ALTER TABLE models ADD COLUMN user_base_model TEXT",
)

_URL = (
    # a download from a direct address (a Hugging Face file) instead of a civitai version; NULL for civitai
    "ALTER TABLE downloads ADD COLUMN url TEXT",
)

_MIGRATIONS = {
    2: ("ALTER TABLE models ADD COLUMN bundled INTEGER NOT NULL DEFAULT 0",),
    3: (_DOWNLOADS,),
    4: _RETIRE,
    5: _CARDS,
    6: _USER,
    7: _URL,
}

_CARD_KINDS = "('checkpoint', 'diffusion_model', 'lora', 'embedding')"

_SCHEMA = _DOWNLOADS + ";\n" + ";\n".join(_RETIRE + _URL) + """;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  path_key TEXT NOT NULL UNIQUE,          -- the path in the file system's own case rules: the cache key
  path TEXT NOT NULL,
  folder TEXT NOT NULL,                   -- the settings folder it was found under
  rel TEXT NOT NULL,                      -- path inside that folder: ComfyUI's name for the file
  comfy_types TEXT NOT NULL,              -- JSON list: every ComfyUI type the folder stands for
  comfy_type TEXT NOT NULL,               -- the one the file is shown under
  kind TEXT NOT NULL,
  ext TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  sha256 TEXT,                            -- NULL until computed; valid for this path_key + bytes + mtime_ns
  hashed_at REAL,
  hash_error TEXT,
  header TEXT,                            -- JSON from sniff.py, NULL when the header says nothing
  header_done INTEGER NOT NULL DEFAULT 0,
  sidecar TEXT,                           -- JSON summary of the old WebUI files next to the model
  sidecar_sig TEXT NOT NULL DEFAULT '',
  sidecar_imported TEXT NOT NULL DEFAULT '',   -- the sidecar_sig already copied into the model's record
  shadowed INTEGER NOT NULL DEFAULT 0,    -- another file has the same ComfyUI name and is found first
  present INTEGER NOT NULL DEFAULT 1,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);

CREATE TABLE IF NOT EXISTS models (
  sha256 TEXT PRIMARY KEY,
  -- pending | found | not_found | bundled (civitai knows the file only as an extra attached to other models)
  civitai_state TEXT NOT NULL DEFAULT 'pending',
  civitai_site TEXT,
  civitai_checked_at REAL,
  bundled INTEGER NOT NULL DEFAULT 0,     -- how many civitai versions carry the file along without being it
  source TEXT,                            -- where the identification comes from: civitai | sidecar | NULL
  model_id INTEGER,
  version_id INTEGER,
  title TEXT,
  version_name TEXT,
  model_type TEXT,
  base_model TEXT,
  family TEXT,
  trigger_words TEXT NOT NULL DEFAULT '[]',
  verified INTEGER,                       -- 1: the version lists this fingerprint; 0: it does not; NULL: unknown
  cover_state TEXT NOT NULL DEFAULT 'pending',     -- pending | ready | none | failed
  cover_level INTEGER,
  cover_source TEXT,
  card_state TEXT NOT NULL DEFAULT 'empty',
  card_drafted INTEGER NOT NULL DEFAULT 0,
  user_title TEXT,                        -- the name the user gave the model; NULL: not given
  user_base_model TEXT                    -- the base model the user gave it; NULL: not given
);
"""

_KIND_ORDER = "CASE kind WHEN 'lora' THEN 0 WHEN 'embedding' THEN 1 WHEN 'checkpoint' THEN 2 " \
              "WHEN 'diffusion_model' THEN 3 ELSE 4 END"

_IDENTITY_COLUMNS = ("model_id", "version_id", "title", "version_name", "model_type", "base_model", "family")


def _loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class Index:
    def __init__(self, path):
        self.path = path
        self.conn = None
        self._open()

    # ---- opening ---------------------------------------------------------------------------------------------------
    def _connect(self):
        conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            conn.close()        # or the file stays locked and cannot be set aside
            raise
        return conn

    def _open(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            self.conn = self._connect()
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise sqlite3.DatabaseError("index schema %s is newer than this program" % version)
            if version == 0:
                self.conn.executescript(_SCHEMA)
            else:
                for target in range(version + 1, SCHEMA_VERSION + 1):
                    for statement in _MIGRATIONS[target]:
                        self.conn.execute(statement)
                    log.info("index upgraded to schema %d", target)
            self.conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
            self.conn.execute("SELECT count(*), sum(models.bundled) FROM files JOIN models ON models.sha256 = files.sha256").fetchone()
        except sqlite3.DatabaseError as e:
            # A damaged or foreign index is set aside and a fresh one is built: everything in it can be recomputed.
            log.warning("index unusable (%s); starting a new one", e)
            self._set_aside()
            self.conn = self._connect()
            self.conn.executescript(_SCHEMA)
            self.conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)

    def _set_aside(self):
        if self.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self.conn.close()
            self.conn = None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            src = self.path + suffix
            if os.path.exists(src):
                with contextlib.suppress(OSError):
                    os.replace(src, "%s.broken-%s%s" % (self.path, stamp, suffix))

    def close(self):
        if self.conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self.conn.close()
            self.conn = None

    def clear(self):
        """Empty the index (the "rebuild" button). The next scan fills it again from the files and the records."""
        with self.tx():
            self.conn.execute("DELETE FROM files")
            self.conn.execute("DELETE FROM models")
            self.conn.execute("DELETE FROM meta WHERE key != 'revision'")
            self._bump()

    @contextlib.contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ---- meta ------------------------------------------------------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, str(value)))

    def revision(self):
        return int(self.get_meta("revision", "0"))

    def _bump(self):
        self.set_meta("revision", self.revision() + 1)

    def bump_revision(self):
        self._bump()
        return self.revision()

    # ---- scanning --------------------------------------------------------------------------------------------------
    def known_files(self):
        """What the scanner needs to skip unchanged files: {path_key: {...}}."""
        out = {}
        for r in self.conn.execute("SELECT path_key, bytes, mtime_ns, header, header_done, sidecar, sidecar_sig FROM files"):
            out[r["path_key"]] = {"bytes": r["bytes"], "mtime_ns": r["mtime_ns"], "header": _loads(r["header"]),
                                  "header_done": bool(r["header_done"]), "sidecar": _loads(r["sidecar"]),
                                  "sidecar_sig": r["sidecar_sig"]}
        return out

    def apply_scan(self, found, complete=True, now=None):
        """Bring the files table in line with a scan. Returns {"added", "changed", "removed", "touched"}.

        A file whose size or modification time changed loses its fingerprint. Files not found are marked absent,
        not deleted, so a folder that is switched off and on again keeps its fingerprints. When the scan was cut
        short (`complete` false) nothing is marked absent.
        """
        now = now or time.time()
        counts = {"added": 0, "changed": 0, "removed": 0, "touched": 0}
        with self.tx():
            existing = {r["path_key"]: r for r in self.conn.execute(
                "SELECT id, path_key, path, rel, comfy_type, comfy_types, bytes, mtime_ns, sidecar_sig, shadowed, present FROM files")}
            seen = set()
            for f in found:
                key = os.path.normcase(f["path"])
                seen.add(key)
                header = json.dumps(f["header"], ensure_ascii=False) if f.get("header") is not None else None
                side = json.dumps(f["sidecar"], ensure_ascii=False) if f.get("sidecar") else None
                types = json.dumps(f["comfy_types"])
                row = existing.get(key)
                if row is None:
                    self.conn.execute(
                        "INSERT INTO files(path_key, path, folder, rel, comfy_types, comfy_type, kind, ext, bytes, mtime_ns,"
                        " header, header_done, sidecar, sidecar_sig, shadowed, present, first_seen, last_seen)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,1,?,?)",
                        (key, f["path"], f["folder"], f["rel"], types, f["comfy_type"], f["kind"], f["ext"], f["bytes"],
                         f["mtime_ns"], header, side, f["sidecar_sig"], int(f["shadowed"]), now, now))
                    counts["added"] += 1
                    continue
                content_changed = row["bytes"] != f["bytes"] or row["mtime_ns"] != f["mtime_ns"]
                if content_changed:
                    counts["changed"] += 1
                    self.conn.execute("UPDATE files SET sha256=NULL, hashed_at=NULL, sidecar_imported='' WHERE id=?", (row["id"],))
                elif (row["rel"], row["comfy_type"], row["comfy_types"], row["sidecar_sig"], row["shadowed"], row["present"], row["path"]) \
                        != (f["rel"], f["comfy_type"], types, f["sidecar_sig"], int(f["shadowed"]), 1, f["path"]):
                    counts["touched"] += 1
                self.conn.execute(
                    "UPDATE files SET path=?, folder=?, rel=?, comfy_types=?, comfy_type=?, kind=?, ext=?, bytes=?, mtime_ns=?,"
                    " header=?, header_done=1, sidecar=?, sidecar_sig=?, shadowed=?, present=1, hash_error=NULL, last_seen=?"
                    " WHERE id=?",
                    (f["path"], f["folder"], f["rel"], types, f["comfy_type"], f["kind"], f["ext"], f["bytes"],
                     f["mtime_ns"], header, side, f["sidecar_sig"], int(f["shadowed"]), now, row["id"]))
            if complete:
                gone = [r["id"] for k, r in existing.items() if k not in seen and r["present"]]
                for file_id in gone:
                    self.conn.execute("UPDATE files SET present=0 WHERE id=?", (file_id,))
                counts["removed"] = len(gone)
            self.set_meta("scanned_at", now)
            if any(counts.values()):
                self._bump()
        return counts

    # ---- fingerprints ----------------------------------------------------------------------------------------------
    def pending_hash(self):
        """Files still to fingerprint: small and useful first (LoRAs, then checkpoints), so the list fills in quickly."""
        return self.conn.execute(
            "SELECT id, path, bytes, mtime_ns, kind FROM files WHERE present=1 AND sha256 IS NULL AND hash_error IS NULL"
            " ORDER BY " + _KIND_ORDER + ", bytes").fetchall()

    def hash_totals(self):
        r = self.conn.execute(
            "SELECT count(*) AS files, coalesce(sum(bytes),0) AS bytes,"
            " coalesce(sum(sha256 IS NOT NULL),0) AS done_files,"
            " coalesce(sum(CASE WHEN sha256 IS NOT NULL THEN bytes ELSE 0 END),0) AS done_bytes,"
            " coalesce(sum(hash_error IS NOT NULL),0) AS errors FROM files WHERE present=1").fetchone()
        return dict(r)

    def set_hash(self, file_id, sha256, now=None):
        with self.tx():
            self.conn.execute("UPDATE files SET sha256=?, hashed_at=?, hash_error=NULL WHERE id=?",
                              (sha256, now or time.time(), file_id))
            self.conn.execute("INSERT OR IGNORE INTO models(sha256) VALUES(?)", (sha256,))
            self._bump()

    def set_hash_error(self, file_id, message):
        self.conn.execute("UPDATE files SET hash_error=? WHERE id=?", (str(message)[:300], file_id))

    def file(self, file_id):
        return self.conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()

    def mark_sidecar_imported(self, file_id, sig):
        self.conn.execute("UPDATE files SET sidecar_imported=? WHERE id=?", (sig, file_id))

    def files_to_import(self):
        """Fingerprinted files whose sidecars are not in their model's record yet (or changed since)."""
        return self.conn.execute(
            "SELECT * FROM files WHERE present=1 AND sha256 IS NOT NULL AND sidecar IS NOT NULL"
            " AND sidecar_sig != sidecar_imported").fetchall()

    # ---- models ----------------------------------------------------------------------------------------------------
    def ensure_model(self, sha256):
        self.conn.execute("INSERT OR IGNORE INTO models(sha256) VALUES(?)", (sha256,))

    def model(self, sha256):
        return self.conn.execute("SELECT * FROM models WHERE sha256=?", (sha256,)).fetchone()

    def set_identity(self, sha256, source, fields, verified=None):
        """Record which civitai version a fingerprint is, and where that knowledge comes from."""
        values = [fields.get(c) for c in _IDENTITY_COLUMNS]
        with self.tx():
            self.ensure_model(sha256)
            self.conn.execute(
                "UPDATE models SET source=?, " + ", ".join(c + "=?" for c in _IDENTITY_COLUMNS) +
                ", trigger_words=?, verified=? WHERE sha256=?",
                [source] + values + [json.dumps(fields.get("trigger_words") or [], ensure_ascii=False),
                                     None if verified is None else int(bool(verified)), sha256])
            self._bump()

    def clear_identity(self, sha256, source):
        """Forget an identification that came from `source` (civitai changed its answer)."""
        with self.tx():
            n = self.conn.execute(
                "UPDATE models SET source=NULL, " + ", ".join(c + "=NULL" for c in _IDENTITY_COLUMNS) +
                ", trigger_words='[]', verified=NULL WHERE sha256=? AND source=?", (sha256, source)).rowcount
            if n:
                self._bump()

    def set_bundled(self, sha256, count):
        self.conn.execute("UPDATE models SET bundled=? WHERE sha256=?", (int(count), sha256))

    def kinds_for(self, sha256s):
        """{fingerprint: set of the kinds its files are stored as}."""
        out = {}
        for sha in sha256s:
            out[sha] = {r[0] for r in self.conn.execute("SELECT DISTINCT kind FROM files WHERE sha256=? AND present=1", (sha,))}
        return out

    def unchecked_hashes(self, limit):
        return [r[0] for r in self.conn.execute(
            "SELECT m.sha256 FROM models m WHERE m.civitai_state='pending'"
            " AND EXISTS (SELECT 1 FROM files f WHERE f.sha256=m.sha256 AND f.present=1) ORDER BY m.rowid LIMIT ?", (limit,))]

    def count_unchecked(self):
        return self.conn.execute(
            "SELECT count(*) FROM models m WHERE m.civitai_state='pending'"
            " AND EXISTS (SELECT 1 FROM files f WHERE f.sha256=m.sha256 AND f.present=1)").fetchone()[0]

    def set_civitai_state(self, sha256s, state, site=None, now=None):
        now = now or time.time()
        with self.tx():
            for sha in sha256s:
                self.ensure_model(sha)
                self.conn.execute("UPDATE models SET civitai_state=?, civitai_site=?, civitai_checked_at=? WHERE sha256=?",
                                  (state, site, now, sha))
            if sha256s:
                self._bump()

    def recheck_civitai(self, include_found=False):
        """Ask civitai again: about the fingerprints it could not name, or about all of them."""
        where = "" if include_found else " WHERE civitai_state IN ('not_found', 'bundled')"
        with self.tx():
            n = self.conn.execute("UPDATE models SET civitai_state='pending'" + where).rowcount
            if n:
                self._bump()
        return n

    def cover_candidates(self):
        return self.conn.execute(
            "SELECT m.* FROM models m WHERE m.cover_state='pending'"
            " AND EXISTS (SELECT 1 FROM files f WHERE f.sha256=m.sha256 AND f.present=1)").fetchall()

    def set_cover(self, sha256, state, level=None, source=None):
        with self.tx():
            self.conn.execute("UPDATE models SET cover_state=?, cover_level=?, cover_source=? WHERE sha256=?",
                              (state, level, source, sha256))
            self._bump()

    def retry_covers(self):
        self.conn.execute("UPDATE models SET cover_state='pending' WHERE cover_state IN ('failed', 'none')")

    # ---- cards -----------------------------------------------------------------------------------------------------
    def set_card(self, sha256, state, drafted):
        """What a model's card holds now, and the drafter it was drafted with. The caller bumps the revision."""
        self.conn.execute("UPDATE models SET card_state=?, card_drafted=? WHERE sha256=?", (state, int(drafted), sha256))

    def redraft_card(self, sha256):
        """What the card is drafted from changed: draft it again."""
        self.conn.execute("UPDATE models SET card_drafted=0 WHERE sha256=?", (sha256,))

    def cards_to_draft(self, drafter, limit=500):
        """[(sha256, kind)] of models here whose card is not drafted by this drafter yet. A fingerprint civitai has
        not been asked about waits: its card is drafted from the answer."""
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT m.sha256, min(f.kind) FROM models m JOIN files f ON f.sha256 = m.sha256 AND f.present = 1"
            " WHERE m.card_drafted != ? AND m.civitai_state != 'pending' AND f.kind IN " + _CARD_KINDS +
            " GROUP BY m.sha256 ORDER BY m.rowid LIMIT ?", (int(drafter), limit))]

    def set_user_info(self, sha256, title, base_model):
        """The name and the base model the user gave a model (None: not given), for the lists."""
        with self.tx():
            self.ensure_model(sha256)
            self.conn.execute("UPDATE models SET user_title=?, user_base_model=? WHERE sha256=?", (title, base_model, sha256))
            self._bump()

    def warm_candidates(self):
        """[(sha256, model_id)] of the models here that civitai named, oldest in the library first: what warming up
        reads ahead of time."""
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT m.sha256, m.model_id FROM models m WHERE m.source = 'civitai' AND m.civitai_state = 'found'"
            " AND m.model_id IS NOT NULL AND EXISTS (SELECT 1 FROM files f WHERE f.sha256 = m.sha256 AND f.present = 1)"
            " ORDER BY m.rowid")]

    def named_models(self):
        """[(sha256, model_id, kind)] of the models here that civitai or the old WebUI's files name, oldest in the
        library first: what filling in the details goes through."""
        return [(r[0], r[1], r[2]) for r in self.conn.execute(
            "SELECT m.sha256, m.model_id, min(f.kind) FROM models m JOIN files f ON f.sha256 = m.sha256 AND f.present = 1"
            " WHERE m.source IS NOT NULL GROUP BY m.sha256 ORDER BY m.rowid")]

    def civitai_versions(self):
        """[(sha256, model_id, version_id, title)] of the models here that civitai or the old WebUI's files name, oldest
        in the library first: what naming and checking for new versions go through."""
        return [(r[0], r[1], r[2], r[3]) for r in self.conn.execute(
            "SELECT m.sha256, m.model_id, m.version_id, m.title FROM models m WHERE m.source IS NOT NULL"
            " AND m.model_id IS NOT NULL AND EXISTS (SELECT 1 FROM files f WHERE f.sha256 = m.sha256 AND f.present = 1)"
            " ORDER BY m.rowid")]

    def civitai_titles(self):
        """{model_id: title} of the models here civitai itself names (not only the old WebUI's files): what is named."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT m.model_id, m.title FROM models m WHERE m.source = 'civitai' AND m.model_id IS NOT NULL"
            " AND m.title IS NOT NULL AND EXISTS (SELECT 1 FROM files f WHERE f.sha256 = m.sha256 AND f.present = 1)"
            " ORDER BY m.rowid DESC")}

    def shas_of_model(self, model_id):
        """[(sha256, kind)] of the files here that are versions of this civitai model."""
        return [(r[0], r[1]) for r in self.conn.execute(
            "SELECT m.sha256, min(f.kind) FROM models m JOIN files f ON f.sha256 = m.sha256 AND f.present = 1"
            " WHERE m.model_id = ? GROUP BY m.sha256", (int(model_id),))]

    def mark_absent(self, paths):
        """These files are gone (moved to the recycle bin): they leave the lists now, before the next scan sees it."""
        with self.tx():
            for path in paths:
                self.conn.execute("UPDATE files SET present=0 WHERE path_key=?", (os.path.normcase(path),))
            self._bump()

    def files_for(self, sha256, present_only=True):
        sql = "SELECT * FROM files WHERE sha256=?" + (" AND present=1" if present_only else "") + " ORDER BY shadowed, path"
        return self.conn.execute(sql, (sha256,)).fetchall()

    # ---- reading for the API ---------------------------------------------------------------------------------------
    _JOIN = ("SELECT f.*, m.civitai_state, m.civitai_site, m.bundled, m.source, m.model_id, m.version_id, m.title, m.version_name,"
             " m.model_type, m.base_model, m.family AS model_family, m.trigger_words, m.verified, m.cover_state,"
             " m.cover_level, m.cover_source, m.card_state, m.user_title, m.user_base_model"
             " FROM files f LEFT JOIN models m ON m.sha256 = f.sha256 WHERE f.present=1")

    def joined(self):
        return self.conn.execute(self._JOIN + " ORDER BY f.kind, f.rel COLLATE NOCASE").fetchall()

    def joined_by_sha(self, sha256):
        return self.conn.execute(self._JOIN + " AND f.sha256=? ORDER BY f.shadowed, f.path", (sha256,)).fetchall()

    def joined_by_name(self, rel):
        return self.conn.execute(self._JOIN + " AND f.rel=? COLLATE NOCASE ORDER BY f.shadowed, f.path", (rel,)).fetchall()

    def present_hashes(self):
        """{fingerprint: ComfyUI name of a file that has it}, for the files that are here."""
        out = {}
        for r in self.conn.execute("SELECT sha256, rel FROM files WHERE present=1 AND sha256 IS NOT NULL ORDER BY shadowed, path"):
            out.setdefault(r[0], r[1])
        return out

    def file_by_key(self, path_key):
        return self.conn.execute("SELECT * FROM files WHERE path_key=?", (path_key,)).fetchone()

    # ---- the download queue ----------------------------------------------------------------------------------------
    _DL_ACTIVE = "('queued', 'running', 'verifying', 'paused', 'retrying')"

    def dl_insert(self, fields):
        now = time.time()
        fields = dict(fields, created_at=now, updated_at=now, position=fields.get("position") or now)
        keys = sorted(fields)
        cur = self.conn.execute("INSERT INTO downloads(%s) VALUES(%s)" % (", ".join(keys), ", ".join("?" * len(keys))),
                                [fields[k] for k in keys])
        return cur.lastrowid

    def dl_update(self, download_id, **fields):
        fields["updated_at"] = time.time()
        keys = sorted(fields)
        self.conn.execute("UPDATE downloads SET %s WHERE id=?" % ", ".join(k + "=?" for k in keys),
                          [fields[k] for k in keys] + [download_id])

    def dl_get(self, download_id):
        return self.conn.execute("SELECT * FROM downloads WHERE id=?", (download_id,)).fetchone()

    def dl_list(self):
        return self.conn.execute("SELECT * FROM downloads ORDER BY position, id").fetchall()

    def dl_delete(self, download_id):
        self.conn.execute("DELETE FROM downloads WHERE id=?", (download_id,))

    def dl_next(self, now):
        """The next download to start: queued, or waiting to be tried again and due."""
        return self.conn.execute(
            "SELECT * FROM downloads WHERE state='queued' OR (state='retrying' AND coalesce(next_try_at, 0) <= ?)"
            " ORDER BY position, id LIMIT 1", (now,)).fetchone()

    def dl_next_wait(self, now):
        """Seconds until the earliest retry is due, or None."""
        r = self.conn.execute("SELECT min(next_try_at) FROM downloads WHERE state='retrying'").fetchone()[0]
        return None if r is None else max(0.0, r - now)

    def dl_reset_interrupted(self):
        """Downloads that were running when the program stopped go back to the queue; their .part files remain."""
        return self.conn.execute("UPDATE downloads SET state='queued' WHERE state IN ('running', 'verifying')").rowcount

    def dl_active_for_file(self, file_id):
        return self.conn.execute("SELECT * FROM downloads WHERE file_id=? AND state IN " + self._DL_ACTIVE,
                                 (file_id,)).fetchone()

    def dl_active_files(self):
        return {r[0] for r in self.conn.execute("SELECT file_id FROM downloads WHERE state IN " + self._DL_ACTIVE)}

    def dl_active_paths(self):
        """normcased destination paths promised to downloads that are not finished."""
        return {os.path.normcase(os.path.join(r[0], r[1])) for r in self.conn.execute(
            "SELECT dest_dir, dest_name FROM downloads WHERE state IN " + self._DL_ACTIVE)}

    def model_counts(self):
        """How the fingerprints of the files that are here stand with civitai and with covers."""
        r = self.conn.execute(
            "SELECT coalesce(sum(civitai_state='found'),0) AS found,"
            " coalesce(sum(civitai_state='not_found'),0) AS not_found,"
            " coalesce(sum(civitai_state='bundled'),0) AS bundled,"
            " coalesce(sum(civitai_state='pending'),0) AS unchecked,"
            " coalesce(sum(cover_state='ready'),0) AS covers_ready,"
            " coalesce(sum(cover_state='failed'),0) AS covers_failed FROM models m"
            " WHERE EXISTS (SELECT 1 FROM files f WHERE f.sha256=m.sha256 AND f.present=1)").fetchone()
        return dict(r)
