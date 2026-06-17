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
(schema change), it logs and skips rather than blocking on a dialog.
"""

import os

from aqt import gui_hooks, mw
from aqt.qt import QTimer

SYNC_INTERVAL_MS = 60_000  # background sync cadence
INITIAL_SYNC_DELAY_MS = 5_000  # let the collection finish loading first


def _log(msg: str) -> None:
    print(f"[flashgen-sync] {msg}", flush=True)


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
    user = os.environ.get("ANKIWEB_USERNAME", "").strip()
    pw = os.environ.get("ANKIWEB_PASSWORD", "")
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
        # Anki returns a "required" enum: NO_CHANGES / NORMAL_SYNC are handled by
        # sync_collection itself; FULL_SYNC/FULL_UPLOAD/FULL_DOWNLOAD would need a
        # one-way choice we must NOT prompt for headlessly.
        name = getattr(required, "name", str(required))
        if name and "FULL" in name.upper():
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
