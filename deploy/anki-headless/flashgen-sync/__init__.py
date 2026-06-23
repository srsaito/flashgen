"""FlashGen sync bootstrap — headless AnkiWeb login + background auto-sync.

Why this exists: Anki 24.11 only logs into AnkiWeb through the GUI and stores the
sync key in the OS keyring/secret-service, which doesn't exist in a headless
container. So a containerized Anki boots unauthenticated and AnkiConnect's `sync`
silently no-ops — cards created via AnkiConnect never reach AnkiWeb.

This addon, on profile open:
  1. If ANKIWEB_USERNAME/ANKIWEB_PASSWORD are set and no sync key is stored,
     logs in programmatically (the Anki-maintainer-endorsed method) and persists
     the key to the profile (prefs21.db) so it survives container restarts.
  2. Runs a periodic background sync (and one shortly after open) so cards added
     via AnkiConnect are pushed to AnkiWeb automatically — no GUI dialogs.

It is headless-safe: it never opens a modal. If a full sync is ever required
(schema change) on a collection that ALREADY HAS DATA, it logs and skips rather
than risking a one-way overwrite headlessly.

The one exception is disaster recovery: if the anki-data volume is wiped or
recreated, the local collection boots EMPTY and the server requires a full sync
to seed it. With no local data to lose, this addon performs the initial
FULL_DOWNLOAD automatically so the container repopulates from AnkiWeb instead of
coming up empty. (An empty collection must NEVER full-upload — that would wipe
AnkiWeb — so when local is empty we always download, never upload.)
"""

import os

from aqt import gui_hooks, mw
from aqt.qt import QTimer

SYNC_INTERVAL_MS = 60_000  # background sync cadence
INITIAL_SYNC_DELAY_MS = 5_000  # let the collection finish loading first


def _log(msg: str) -> None:
    print(f"[flashgen-sync] {msg}", flush=True)


def _secret(name: str) -> str:
    """Read a secret, preferring a Docker/Compose secret file over a raw env var.

    If <NAME>_FILE is set (e.g. ANKIWEB_PASSWORD_FILE=/run/secrets/ankiweb_password),
    read and return the file's contents (trailing newline stripped — secret files
    conventionally end in one). Otherwise fall back to the <NAME> env var. Using a
    secret file keeps the value out of the container environment and `docker inspect`.
    """
    path = os.environ.get(f"{name}_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            _log(f"could not read {name}_FILE ({path}): {exc!r} — falling back to {name} env")
    return os.environ.get(name, "")


def _current_auth():
    """Return stored SyncAuth (has .hkey) or None."""
    try:
        auth = mw.pm.sync_auth()
    except Exception as exc:  # pragma: no cover - defensive
        _log(f"sync_auth() unavailable: {exc!r}")
        return None
    if auth and getattr(auth, "hkey", None):
        return auth
    return None


def _ensure_login() -> bool:
    """Authenticate to AnkiWeb.

    If credentials are set, ALWAYS (re)log in and refresh the stored sync key —
    this makes auth durable by construction (works even if the key didn't survive
    a SIGKILL or the volume was recreated), rather than depending on key
    persistence. Falls back to any existing stored key if login fails.
    """
    user = _secret("ANKIWEB_USERNAME").strip()
    pw = _secret("ANKIWEB_PASSWORD")
    if user and pw:
        try:
            endpoint = mw.pm.sync_endpoint()
            auth = mw.col.sync_login(username=user, password=pw, endpoint=endpoint)
            mw.pm.set_sync_key(auth.hkey)
            mw.pm.set_sync_username(user)
            _log(f"logged into AnkiWeb as {user}; sync key refreshed")
            return True
        except Exception as exc:
            _log(f"AnkiWeb login FAILED: {exc!r} — falling back to stored key")

    if _current_auth():
        _log("using existing stored sync key")
        return True

    _log("no AnkiWeb credentials and no stored sync key — auto-sync disabled")
    return False


# Anki's sync "required" field tells us what kind of sync the server wants.
# Depending on the Anki build it arrives either as a protobuf enum object (with a
# .name) OR as a RAW INT — observed live as int 3 (FULL_DOWNLOAD), which silently
# defeated an earlier `.name`-only check (`"FULL" in "3"` is False) and skipped
# the very download this addon exists to perform. Handle both. Values 2/3/4 all
# mean a one-way full sync is required.
_REQUIRED_NAMES = {
    0: "NO_CHANGES",
    1: "NORMAL_SYNC",
    2: "FULL_SYNC",
    3: "FULL_DOWNLOAD",
    4: "FULL_UPLOAD",
}
_FULL_REQUIRED = {2, 3, 4}


def _required_info(required):
    """Normalize the sync 'required' field to (value:int|None, name:str, is_full:bool)."""
    val = None
    try:
        val = int(required)
    except (TypeError, ValueError):
        pass
    name = getattr(required, "name", None) or _REQUIRED_NAMES.get(val, str(required))
    is_full = (val in _FULL_REQUIRED) or ("FULL" in str(name).upper())
    return val, name, is_full


def _collection_is_empty() -> bool:
    """True only when the local collection has no notes and no cards.

    A freshly created collection (e.g. after the anki-data volume was wiped)
    reports zero cards/notes even though it ships one default deck. We fail
    SAFE: if the size can't be read, treat the collection as non-empty so we
    never auto-download over data we couldn't account for.
    """
    try:
        cards = mw.col.card_count()
        notes = mw.col.note_count()
    except Exception as exc:
        _log(f"could not read collection size: {exc!r} — treating as non-empty")
        return False
    return cards == 0 and notes == 0


def _full_download(auth, out) -> None:
    """Seed an EMPTY local collection from AnkiWeb (one-way download).

    Mirrors what aqt.sync.full_download does for the GUI, minus the dialogs:
    close the collection for a full sync, pull the server copy, then reopen.
    Only ever called when _collection_is_empty() is True, so there is no local
    data to lose. Runs synchronously on the main thread (same as the normal
    background sync above) — acceptable headlessly where there is no UI to block.
    """
    try:
        server_usn = out.server_media_usn if mw.pm.media_syncing_enabled() else None
    except Exception:
        server_usn = None
    mw.col.close_for_full_sync()
    try:
        mw.col.full_upload_or_download(auth=auth, server_usn=server_usn, upload=False)
    finally:
        # Always reopen, even if the download raised, so AnkiConnect stays alive.
        mw.col.reopen(after_full_sync=True)
    mw.reset()
    _log("initial FULL_DOWNLOAD complete — collection seeded from AnkiWeb")


def _background_sync() -> None:
    """Run one normal (incremental) collection sync; never opens a dialog."""
    auth = _current_auth()
    if not auth:
        return
    try:
        try:
            out = mw.col.sync_collection(auth, True)  # (auth, sync_media)
        except TypeError:
            out = mw.col.sync_collection(auth)  # older/newer arity fallback
        required = getattr(out, "required", None)
        # NO_CHANGES / NORMAL_SYNC are handled by sync_collection itself;
        # FULL_SYNC/FULL_UPLOAD/FULL_DOWNLOAD would need a one-way choice we must
        # NOT prompt for headlessly.
        _, name, is_full = _required_info(required)
        if is_full:
            # Disaster recovery: a wiped/recreated volume boots EMPTY and the
            # server demands a full sync to seed it. With nothing to lose, pull
            # the server copy. (Never upload an empty collection — that wipes
            # AnkiWeb — so emptiness gates a DOWNLOAD regardless of the exact
            # FULL_* the server reported.)
            if _collection_is_empty():
                _log(f"full sync required ({name}) and local collection is empty — downloading from AnkiWeb")
                _full_download(auth, out)
            else:
                _log(f"full sync required ({name}) — skipping headlessly (manual resolution needed)")
        else:
            _log(f"sync ok (required={name})")
    except Exception as exc:
        _log(f"background sync error: {exc!r}")


def _on_profile_open() -> None:
    _ensure_login()
    QTimer.singleShot(INITIAL_SYNC_DELAY_MS, _background_sync)
    timer = QTimer(mw)
    timer.setInterval(SYNC_INTERVAL_MS)
    timer.timeout.connect(_background_sync)
    timer.start()
    mw._flashgen_sync_timer = timer  # keep a reference so it isn't GC'd
    _log("auto-sync scheduled")


gui_hooks.profile_did_open.append(_on_profile_open)
