#!/usr/bin/env python3
"""Generate a seed ``prefs21.db`` so a FRESH anki-data volume boots headlessly.

Why this exists (flashgen-2b9): when the anki-data volume is wiped/recreated,
Anki's first launch has no stored language preference and pops a modal
"Language" selection dialog (then an "Are you sure…?" confirm). Under Xvfb there
is no one to click it, so Anki blocks at "Initial setup…" forever — AnkiConnect
never starts and the flashgen-sync addon's ``profile_did_open`` hook never fires,
so the automatic FULL_DOWNLOAD recovery can't even begin.

Pre-seeding the ProfileManager meta with ``defaultLang`` set and
``firstRun=False`` makes Anki skip the dialog and open the "User 1" profile
straight away. The collection is still empty, so the addon then performs the
initial FULL_DOWNLOAD from AnkiWeb.

This contains NO secrets — ``syncKey`` is None; the addon logs into AnkiWeb
itself using ANKIWEB_USERNAME/ANKIWEB_PASSWORD. entrypoint.sh copies the
generated DB into a fresh volume only when no prefs21.db exists yet.

The shape (a ``profiles`` table of pickled dicts keyed by name, with a special
``_global`` meta row) mirrors a real Anki-generated prefs21.db. Run:

    python3 seed_prefs.py /path/to/prefs21.db
"""

import pickle
import sqlite3
import sys

# Deterministic, instance-independent constants. Real Anki stores wall-clock
# timestamps and a random profile id here; fixed values keep the build
# reproducible and are equally valid (Anki overwrites them as it runs).
_FIXED_EPOCH = 1700000000

# The "_global" meta row. defaultLang + firstRun=False are what suppress the
# first-run language dialog. updates/suppressUpdate are set so no update-check
# prompt appears either (belt-and-suspenders alongside `anki --no-auto-update`).
META = {
    "created": _FIXED_EPOCH,
    "defaultLang": "en_US",
    "firstRun": False,
    "id": 1,
    "lastMsg": 0,
    "last_loaded_profile_name": "User 1",
    "last_run_version": 241100,
    "suppressUpdate": True,
    "updates": False,
    "ver": 0,
}

# A complete "User 1" profile dict using Anki's defaults (captured from a real
# profile), so opening it never KeyErrors on a missing key. syncKey is None on
# purpose — the addon refreshes it via a fresh AnkiWeb login on every start.
PROFILE = {
    "allowHTML": False,
    "autoSync": True,
    "deleteMedia": False,
    "importMode": 1,
    "lastColour": "#00f",
    "lastOptimize": _FIXED_EPOCH,
    "mainWindowGeom": None,
    "mainWindowState": None,
    "numBackups": 50,
    "searchHistory": [],
    "stripHTML": True,
    "syncKey": None,
    "syncMedia": True,
}


def write_seed(path: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS profiles "
            "(name TEXT PRIMARY KEY COLLATE NOCASE, data BLOB NOT NULL)"
        )
        # protocol=4 is readable by every Python 3 Anki has ever bundled.
        rows = [
            ("_global", pickle.dumps(META, protocol=4)),
            ("User 1", pickle.dumps(PROFILE, protocol=4)),
        ]
        con.executemany(
            "INSERT OR REPLACE INTO profiles (name, data) VALUES (?, ?)", rows
        )
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: seed_prefs.py /path/to/prefs21.db")
    write_seed(sys.argv[1])
    print(f"wrote seed prefs21.db -> {sys.argv[1]}")
