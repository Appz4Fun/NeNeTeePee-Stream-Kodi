# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""NZBGet JSON-RPC client: submit an NZB, poll status, resolve completion.

Mirrors nzbdav_api conventions: settings-getter injectable for tests,
``(value, error)`` tuple returns, redacted logging. RPC endpoint is
``<nzbget_url>/jsonrpc`` with HTTP Basic auth.
"""

import base64
import json

import xbmc
import xbmcaddon

from resources.lib.exact_job import ExactJobLookup
from resources.lib.http_util import http_download as _http_download
from resources.lib.http_util import http_get as _http_get
from resources.lib.http_util import http_post_json as _http_post_json
from resources.lib.http_util import redact_text as _redact_text
from resources.lib.nzbget_fleet_identity import release_name

_RPC_TIMEOUT = 30

# settings.xml schema defaults. The injected ``settings_getter``
# (``_get_script_setting`` on the RunScript/widget path) reads the raw profile
# XML, where a setting left at its displayed default is simply absent—so it
# returns the fallback passed in. Mirror the schema defaults here, or a user who
# enables NZBGet + sets the SMB root but leaves the URL/username untouched is
# sent down the NZBGet path only to fail "not configured."
_DEFAULT_URL = "http://localhost:6789"
_DEFAULT_USER = "nzbget"


def _get_settings(settings_getter=None):
    if settings_getter is None:
        addon = xbmcaddon.Addon("plugin.video.nzbdav")
        url = addon.getSetting("nzbget_url").strip().rstrip("/")
        user = addon.getSetting("nzbget_username").strip()
        password = addon.getSetting("nzbget_password")
        category = addon.getSetting("nzbget_category").strip()
    else:
        url = settings_getter("nzbget_url", _DEFAULT_URL).strip().rstrip("/")
        user = settings_getter("nzbget_username", _DEFAULT_USER).strip()
        password = settings_getter("nzbget_password", "")
        category = settings_getter("nzbget_category", "").strip()
    return url, user, password, category


def _rpc_url(base_url):
    return "{}/jsonrpc".format(base_url)


def _rpc_call(
    method, params, settings_getter=None, timeout=_RPC_TIMEOUT, encoded_payload=None
):
    """Invoke a JSON-RPC method. Returns (result, error).

    On success: (result_value, None). On any failure: (None, message_str).
    """
    base_url, user, password, _category = _get_settings(settings_getter)
    if not base_url:
        return None, "not_configured"
    # ``id`` MUST precede ``params``: NZBGet's legacy JSON-RPC parser can
    # mis-parse fields that appear after ``params`` (maintainer note,
    # forum.nzbget.net t=2209), making append/poll RPCs fail or pick up an
    # extra parameter. Order the payload id -> method -> params accordingly.
    payload = (
        encoded_payload
        if encoded_payload is not None
        else {"id": 1, "method": method, "params": list(params)}
    )
    try:
        text = _http_post_json(
            _rpc_url(base_url),
            payload,
            timeout=timeout,
            basic_auth=(user, password),
        )
        data = json.loads(text)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: NZBGet {} failed: {}").format(
                method, _redact_text(str(exc))
            ),
            xbmc.LOGERROR,
        )
        return None, _redact_text(str(exc))
    if isinstance(data, dict) and data.get("error"):
        message = _redact_text(str(data["error"]))
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet {} error: {}".format(method, message),
            xbmc.LOGERROR,
        )
        return None, RpcError(message, data["error"])
    return data.get("result") if isinstance(data, dict) else None, None


class RpcError(str):
    """Redacted error text retaining the JSON-RPC code for capability checks."""

    def __new__(cls, message, detail):
        obj = super().__new__(cls, message)
        obj.code = detail.get("code") if isinstance(detail, dict) else None
        obj.rpc_message = (
            detail.get("message", "") if isinstance(detail, dict) else str(detail)
        )
        return obj


def is_unknown_method(error):
    """Only a server JSON-RPC method error permits retrying via append."""
    if not isinstance(error, RpcError):
        return False
    message = str(error.rpc_message).strip().lower()
    return error.code == -32601 or any(
        message == name or message.startswith(name + ":")
        for name in (
            "invalid method",
            "invalid procedure",
            "unknown method",
            "method not found",
        )
    )


def append_fleet(members, dupe_key, settings_getter=None):
    """Submit 1..50 ordered, deduplicated manifests for server health ranking."""
    if not 1 <= len(members) <= 50:
        return None, "appendfleet requires 1 to 50 members"
    category = _get_settings(settings_getter)[3]
    params = [
        {
            "DupeKey": dupe_key,
            "Category": category,
            "Priority": 0,
            "Timeout": 45,
            "Members": members,
        }
    ]
    from resources.lib.nzbget_fleet_payload import fleet_payload

    try:
        with fleet_payload(params) as payload:
            _log_fleet_size(params, payload)
            result, error = _rpc_call(
                "appendfleet",
                params,
                settings_getter=settings_getter,
                timeout=300,
                encoded_payload=payload,
            )
    except (OSError, TypeError, ValueError) as exc:
        return None, _redact_text(str(exc))
    if error is not None:
        return None, error
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("Chosen"), int)
        or isinstance(result.get("Chosen"), bool)
        or result["Chosen"] < 0
    ):
        return None, "Invalid appendfleet response; submission may have succeeded"
    rows = result.get("Members")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None, "Invalid appendfleet members; submission may have succeeded"
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: NZBGet appendfleet Chosen={} Alive={}".format(
            result["Chosen"], [row.get("Alive", -1) for row in rows]
        ),
        xbmc.LOGINFO,
    )
    return result, None


def _log_fleet_size(params, payload):
    """Log only size and count; URL credentials and base64 content stay private."""
    if payload is not None:
        payload.seek(0, 2)
        size = payload.tell()
        payload.seek(0)
    else:
        request = {"id": 1, "method": "appendfleet", "params": params}
        size = sum(
            len(chunk.encode("utf-8"))
            for chunk in json.JSONEncoder().iterencode(request)
        )
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: NZBGet appendfleet request bytes={} members={}".format(
            size, len(params[0]["Members"])
        ),
        xbmc.LOGINFO,
    )


# Same ceiling as the nzbdav manifest fetch: a broken or hostile indexer
# response must not be buffered whole on a small CoreELEC box (the duplicate
# fleet prefetches several at once).
_MAX_NZB_BYTES = 100 * 1024 * 1024


def _fetch_nzb_bytes(nzb_url, max_bytes=_MAX_NZB_BYTES):
    """Fetch the NZB body. Returns bytes. Raises on failure or over the cap."""
    body = _http_get(nzb_url, timeout=_RPC_TIMEOUT, max_bytes=max_bytes)
    if isinstance(body, str):
        body = body.encode("utf-8")
    return body


def fetch_nzb_bytes(nzb_url, max_bytes=_MAX_NZB_BYTES):
    """Public NZB fetch for callers that inspect the body before ``append_nzb``.

    Raises on failure (or past ``max_bytes``), like ``_fetch_nzb_bytes``.
    """
    return _fetch_nzb_bytes(nzb_url, max_bytes=max_bytes)


def download_nzb(nzb_url, dest_path, max_bytes=_MAX_NZB_BYTES):
    """Stream an NZB straight to ``dest_path`` (never fully in memory).

    Raises on failure or past ``max_bytes``; a partial file is removed.
    """
    return _http_download(nzb_url, dest_path, timeout=_RPC_TIMEOUT, max_bytes=max_bytes)


def _append_params(
    nzb_name, nzb_bytes, category, dupe_key="", dupe_score=0, dupe_mode="SCORE"
):
    """Build NZBGet's modern 11-arg ``append`` params list.

    append(NZBFilename, Content, Category, Priority, AddToTop, AddPaused,
           DupeKey, DupeScore, DupeMode, AutoCategory, PPParameters)

    The trailing AutoCategory + PPParameters args are required by NZBGet's
    modern append signature (nzbget.com v16+, verified against a live 26.1
    box): dropping them makes NZBGet reject the whole call with
    ``Invalid parameter (Parameters)`` (JSON-RPC code 2) and the NZB never
    enters the queue. AutoCategory=False keeps the explicit category passed in
    (the SMB completed-path mapping in nzbget_resolver depends on it rather
    than NZBGet auto-reassigning one); PPParameters=[] = no extra
    post-processing parameters.

    ``dupe_key``/``dupe_score``/``dupe_mode`` implement NZBGet Smart Duplicates
    (#372, per nzbget.com/documentation/rss/#duplicates). Defaults ``""``/``0``/
    ``"SCORE"`` reproduce the pre-#372 single submit exactly; a fleet submit
    passes a shared DupeKey (identifying the release) plus a per-item DupeScore
    (pick highest) so NZBGet downloads the highest score and parks the rest in
    history as backups, failing over on an unrepairable download.
    """
    content_b64 = base64.b64encode(nzb_bytes).decode("ascii")
    filename = "{}.nzb".format(nzb_name or "submission")
    return [
        filename,
        content_b64,
        category,
        0,
        False,
        False,
        dupe_key or "",
        int(dupe_score or 0),
        (dupe_mode or "SCORE").upper(),
        False,
        [],
    ]


def append_nzb(
    nzb_url,
    nzb_name,
    settings_getter=None,
    dupe_key="",
    dupe_score=0,
    dupe_mode="SCORE",
    nzb_bytes=None,
):
    """Fetch the NZB and submit it to NZBGet via append.

    Returns (nzbid, error). On success (int > 0, None); on failure
    (None, message). ``dupe_key``/``dupe_score``/``dupe_mode`` drive NZBGet
    Smart Duplicates (#372); their defaults reproduce the pre-#372 single submit.
    ``nzb_bytes`` skips the fetch when the caller already holds the body.
    """
    _base_url, _user, _password, category = _get_settings(settings_getter)
    try:
        if nzb_bytes is None:
            nzb_bytes = _fetch_nzb_bytes(nzb_url)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: NZBGet NZB fetch failed: {}").format(
                _redact_text(str(exc))
            ),
            xbmc.LOGERROR,
        )
        return None, _redact_text(str(exc))
    params = _append_params(
        nzb_name, nzb_bytes, category, dupe_key, dupe_score, dupe_mode
    )
    result, error = _rpc_call("append", params, settings_getter=settings_getter)
    if error is not None:
        return None, error
    try:
        nzbid = int(result)
    except (TypeError, ValueError):
        nzbid = 0
    if nzbid <= 0:
        return None, "append returned {!r}".format(result)
    return nzbid, None


def config_option(name, settings_getter=None):
    """Read one running-config option's value via the ``config`` RPC.

    NZBGet's ``config`` returns the live merged config as a list of
    ``{"Name": <name>, "Value": <value>}`` structs (options with fixed value sets come
    back lower-cased). Returns the matched value lower-cased, or ``None`` when the
    option is absent or the RPC fails -- callers treat it as best-effort (for example,
    the #372 HealthCheck=pause warning, per nzbget.com/documentation/rss).
    """
    rows, error = _rpc_call("config", [], settings_getter=settings_getter)
    if error is not None or not isinstance(rows, list):
        return None
    target = str(name or "").lower()
    for row in rows:
        if isinstance(row, dict) and str(row.get("Name", "")).lower() == target:
            return str(row.get("Value", "")).strip().lower()
    return None


def _as_number(value):
    """Coerce an NZBGet size field to a float, tolerating str/None.

    NZBGet (and some proxy layers) serialize size fields as decimal
    strings; the raw ``downloaded * 100 / total`` math would raise
    ``TypeError`` on a str. Fall back to 0 so a malformed/odd field
    degrades to 0% instead of aborting the whole poll on one tick.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _same_nzbid(left, right):
    """Compare two NZBID values tolerating str/int mismatch.

    Some NZBGet builds / proxy layers serialize ``NZBID`` as a decimal string
    while callers pass an int; a strict ``==`` would then treat the job as
    absent and let the poll fall through to a bogus timeout/failure.
    """
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def group_status(nzbid, settings_getter=None):
    """Look up an active job by NZBID via listgroups.

    Returns a dict: {"present": bool, "status": str, "percent": int}.
    "present" is False once the job leaves the active queue (moved to
    history). On RPC error, returns status="ERROR" with present=False.
    """
    groups, error = _rpc_call("listgroups", [0], settings_getter=settings_getter)
    if error is not None or not isinstance(groups, list):
        return {"present": False, "status": "ERROR", "percent": 0}
    for group in groups:
        if not isinstance(group, dict):
            continue
        if _same_nzbid(group.get("NZBID"), nzbid):
            downloaded = _as_number(group.get("DownloadedSizeMB"))
            total = _as_number(group.get("FileSizeMB"))
            percent = int(downloaded * 100 / total) if total else 0
            return {
                "present": True,
                "status": str(group.get("Status") or ""),
                "percent": percent,
            }
    return {"present": False, "status": "", "percent": 0}


def history_status(nzbid, settings_getter=None):
    """Look up a terminal job by NZBID via history.

    Returns {"present": bool, "success": bool, "status": str,
    "dest_dir": str, "nzbid": object, "job_name": str}. "success" is True
    ONLY for SUCCESS/* statuses, per the
    spec's completion guarantee (full post-processing = a repaired, unpacked,
    playable file). WARNING/* (incl. WARNING/REPAIRABLE / WARNING/DAMAGED,
    where par2 repair did not run) is deliberately treated as a failure
    rather than risk playing a corrupt file—the job is left in history so
    it can be retried.
    """
    hist, error = _rpc_call("history", [False], settings_getter=settings_getter)
    if error is not None or not isinstance(hist, list):
        return {"present": False, "success": False, "status": "", "dest_dir": ""}
    for item in hist:
        if not isinstance(item, dict):
            continue
        if _same_nzbid(item.get("NZBID"), nzbid):
            status = str(item.get("Status") or "")
            return {
                "present": True,
                "success": status.startswith("SUCCESS"),
                "status": status,
                "dest_dir": _dest_dir(item),
                "nzbid": item.get("NZBID"),
                "job_name": release_name(item.get("Name")),
            }
    return {"present": False, "success": False, "status": "", "dest_dir": ""}


def _fetch_nzbget_history(settings_getter):
    """Fetch NZBGet history for exact lookup, preserving transient failures."""
    try:
        history, error = _rpc_call("history", [False], settings_getter=settings_getter)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet exact history lookup failed: {}".format(
                _redact_text(str(exc))
            ),
            xbmc.LOGDEBUG,
        )
        return None
    if error is not None or not isinstance(history, list):
        return None
    return history


def _exact_nzbget_identifier(item):
    """Return a non-empty NZBID, or ``None`` when the history row is malformed."""
    if not isinstance(item, dict):
        return None
    identifier = item.get("NZBID")
    return identifier if identifier not in (None, "") else None


def _matched_nzbget_history_result(item):
    """Classify one NZBGet history row already matched by identifier."""
    status = item.get("Status")
    if not isinstance(status, str) or not status:
        return ExactJobLookup.transient()
    if status != "SUCCESS" and not status.startswith("SUCCESS/"):
        return ExactJobLookup.stale()
    dest_dir = _dest_dir(item)
    if not isinstance(dest_dir, str) or not dest_dir:
        return ExactJobLookup.transient()
    return ExactJobLookup.valid(
        {
            "nzbid": item.get("NZBID"),
            "name": release_name(item.get("Name")),
            "status": status,
            "dest_dir": dest_dir,
        }
    )


def _exact_nzbget_history_result(history, nzbid):
    """Classify validated NZBGet history for one exact identifier."""
    malformed = False
    for item in history:
        identifier = _exact_nzbget_identifier(item)
        if identifier is None:
            malformed = True
            continue
        if _same_nzbid(identifier, nzbid):
            return _matched_nzbget_history_result(item)
    return ExactJobLookup.transient() if malformed else ExactJobLookup.stale()


def lookup_completed_job_exact(nzbid, settings_getter=None):
    """Tri-state lookup of one exact NZBGet SUCCESS history identifier."""
    history = _fetch_nzbget_history(settings_getter)
    if history is None:
        return ExactJobLookup.transient()
    return _exact_nzbget_history_result(history, nzbid)


def completed_by_id(nzbid, settings_getter=None):
    """Return the shared exact-job lookup contract for one NZBGet ID."""
    return lookup_completed_job_exact(nzbid, settings_getter=settings_getter)


class _CompletedHistory(dict):
    """Name-keyed SUCCESS history mapping, marked when the lookup succeeded.

    Mirrors nzbdav_api's completed-jobs marker: ``router._completed_lookup_was_done``
    tells "lookup ran and found nothing" (skip per-selection re-lookups) apart
    from "lookup failed" via the ``_lookup_done`` attribute.
    """

    def __init__(self, *args, **kwargs):
        lookup_done = kwargs.pop("lookup_done", False)
        super().__init__(*args, **kwargs)
        self._lookup_done = bool(lookup_done)


def _dest_dir(item):
    """Return a history item's destination dir, preferring FinalDir.

    A post-processing script that moves the output sets FinalDir to the final
    location while DestDir stays the original download dir; prefer FinalDir
    when present so the SMB target maps to where the playable file landed.
    """
    return item.get("FinalDir") or item.get("DestDir") or ""


def _history_item_bytes(item):
    """Best-effort byte size of a history item, or None when unknown.

    Prefers the exact FileSizeHi/FileSizeLo 64-bit pair, falling back to the
    rounded FileSizeMB; tolerates str/None fields like the other parsers. A
    None return makes the picker's size gate fail open (name-only match)
    instead of treating the row as a zero-byte mismatch.
    """
    size = int(_as_number(item.get("FileSizeHi"))) * 4294967296 + int(
        _as_number(item.get("FileSizeLo"))
    )
    if size > 0:
        return size
    size_mb = int(_as_number(item.get("FileSizeMB")))
    if size_mb > 0:
        return size_mb * 1048576
    return None


# Picker-render RPC bound: ``completed_history`` runs synchronously right
# before the results dialog opens, so cap the wait the way the nzbdav picker
# path does (nzbdav_api._API_READ_TIMEOUT = 10, "prevent dialog freeze")
# instead of the resolver-path _RPC_TIMEOUT—a silently unreachable NZBGet
# box must not freeze the picker for 30 seconds.
_PICKER_RPC_TIMEOUT = 10


def completed_history(settings_getter=None):
    """Fetch NZBGet's SUCCESS/* history keyed by job name.

    Powers the picker's "DL" tag in NZBGet mode: a result whose title matches
    a SUCCESS history row already has its finished files on disk, and the
    resolver reuses that row's ``dest_dir`` over SMB on selection instead of
    re-submitting (NZBGet's duplicate check would dupe-delete a re-submission
    of a SUCCESS item, failing the resolve). Values carry the same
    ``name``/``bytes`` shape as nzbdav completed jobs so the router reuses
    its size gate. Returns a lookup_done-marked mapping on RPC success (even
    when empty) and a plain empty dict on any failure—never raises (the
    picker render path has no try/except around tagging). One ``history`` RPC
    per call—same visible-only view as ``history_status``; avoid calling in
    tight loops.
    """
    try:
        hist, error = _rpc_call(
            "history",
            [False],
            settings_getter=settings_getter,
            timeout=_PICKER_RPC_TIMEOUT,
        )
    except Exception as exc:  # pylint: disable=broad-except
        # _rpc_call reads settings before its own try block; a raising
        # injected getter (or an early startup Addon read) must degrade to
        # "no tags," not crash the picker render.
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet completed_history failed: {}".format(
                _redact_text(str(exc))
            ),
            xbmc.LOGDEBUG,
        )
        return {}
    if error is not None or not isinstance(hist, list):
        return {}
    jobs = _CompletedHistory(lookup_done=True)
    for item in hist:
        entry = _completed_job_entry(item)
        if entry is None:
            continue
        # History is newest-first; for same-name SUCCESS rows keep the newest
        # one—that's the row a replay's dupe handling would land on.
        if entry["name"] not in jobs:
            jobs[entry["name"]] = entry
    return jobs


def _completed_job_entry(item):
    """Build a SUCCESS completed-job entry from a history item, or None.

    Returns ``None`` for non-dict rows, non-SUCCESS statuses, or unnamed jobs.
    ``dest_dir`` prefers FinalDir like ``history_status`` so a
    post-processing-script move is followed to the file's final location.
    """
    if not isinstance(item, dict):
        return None
    status = str(item.get("Status") or "")
    if not status.startswith("SUCCESS"):
        return None
    name = release_name(item.get("Name"))
    if not name:
        return None
    return {
        "name": name,
        "status": status,
        "bytes": _history_item_bytes(item),
        "nzbid": item.get("NZBID"),
        "dest_dir": _dest_dir(item),
    }


def completed_base_dir(settings_getter=None):
    """Return NZBGet's configured global completed DestDir (absolute), or None.

    Lets ``nzbget_smb_target`` map a history ``DestDir`` *relative* to NZBGet's
    completed base onto the SMB root, which is exact for any category/custom
    DestDir layout. Best-effort: any RPC failure or a value that isn't an
    absolute path (for example, an unexpanded ``${MainDir}`` template) degrades to None
    so the caller falls back to its folder heuristic.
    """
    cfg, error = _rpc_call("config", [], settings_getter=settings_getter)
    if error is not None or not isinstance(cfg, list):
        return None
    for item in cfg:
        if not isinstance(item, dict):
            continue
        if str(item.get("Name", "")).strip().lower() == "destdir":
            value = str(item.get("Value") or "").strip()
            return value if _is_absolute_path(value) else None
    return None


def _is_absolute_path(value):
    """Return whether ``value`` is an absolute POSIX or Windows path.

    Only an absolute path is trusted; an unexpanded ``${MainDir}`` template
    wouldn't be a reliable prefix of the reported history DestDir.
    """
    return value.startswith("/") or ":\\" in value or value.startswith("\\\\")


def test_connection(settings_getter=None):
    """Probe NZBGet via the version method. Returns ``(success, error)``."""
    result, error = _rpc_call("version", [], settings_getter=settings_getter)
    if error is not None:
        return False, error
    return result is not None, None


def cancel_job(nzbid, settings_getter=None):
    """Delete a job and its files (explicit user cancel).

    Tries the active-queue delete first (GroupFinalDelete removes the job
    and downloaded files), then a history delete in case it already moved
    to history. Best-effort—errors are logged, not raised.
    """
    # editqueue(Command, Args, IDs)—NZBGet v18+ dropped the legacy int
    # ``Offset`` parameter (pre-v18 was ``Command, Offset, Text, IDs``). The
    # target boxes run nzbget.com 16+/26.x, which reject the 4-arg shape and
    # would leave a "canceled" download running; matches the modern 11-arg
    # append signature this client already sends.
    _rpc_call(
        "editqueue",
        ["GroupFinalDelete", "", [nzbid]],
        settings_getter=settings_getter,
    )
    _rpc_call(
        "editqueue",
        ["HistoryFinalDelete", "", [nzbid]],
        settings_getter=settings_getter,
    )


def cancel_jobs(nzbids, settings_getter=None):
    """Final-delete a batch of NZBIDs from history, then the queue (#372 r3).

    Used by the backup worker's post-cancel cleanup: it deletes exactly the
    NZBIDs THIS resolve submitted (never a whole-DupeKey sweep, which could
    wipe a fresh retry of the same release). History first -- the parked hidden
    ``Kind=DUP`` backups -- so nothing is left to promote when the queued ones
    go. IDs are coerced to int (listgroups can serialize NZBID as a string --
    the same tolerance ``_same_nzbid`` applies) and deduped across types:
    ``editqueue`` expects an integer array, and one bad member would fail the
    whole batch. Empty input is a no-op; best-effort like ``cancel_job``.
    Returns True when both deletes went through (or there was nothing to
    delete), False when either RPC failed.
    """
    ids = _int_ids(nzbids)
    if not ids:
        return True
    history_ok = _final_delete("HistoryFinalDelete", ids, settings_getter)
    queue_ok = _final_delete("GroupFinalDelete", ids, settings_getter)
    return history_ok and queue_ok


def cancel_queued_jobs(nzbids, settings_getter=None):
    """Final-delete only the QUEUED members of ``nzbids``; history is kept.

    A canceled play's backups that NZBGet parked in history (``DELETED/DUPE``)
    stay there for a replay to reuse; any that sit in the queue (downloading
    or waiting) are removed so a cancel never leaves a download running. A
    manual final-delete of the pick does not make NZBGet promote a parked
    backup (verified against NZBGet), so the kept ones stay parked. True when
    the delete went through or there was nothing to delete.
    """
    ids = _int_ids(nzbids)
    return _final_delete("GroupFinalDelete", ids, settings_getter) if ids else True


def _int_ids(nzbids):
    """``nzbids`` as unique ints, order kept (``editqueue`` wants an int array)."""
    ids = []
    for nzbid in nzbids or []:
        value = _int_nzbid(nzbid)
        if value is not None and value not in ids:
            ids.append(value)
    return ids


def _dupekey_match(item, dupe_key):
    """Case-insensitive DupeKey compare (NZBGet matches keys case-insensitively)."""
    return (
        str(item.get("DupeKey") or "").strip().lower()
        == str(dupe_key or "").strip().lower()
    )


# Sentinel returned by _promoted_group_entry for a same-key member that is
# queued but PAUSED: not a promotion to track, yet not an exhausted group
# either (for example, NZBGet globally paused when the backup was promoted).
_PAUSED_MATCH = object()


def _promoted_group_entry(group, dupe_key, exclude_nzbid):
    """Classify one listgroups row for the promotion scan.

    Keeps ``active_group_by_dupekey`` a flat scan: the DupeKey match and the
    exclude-NZBID skip (the failed pick itself) live here. Returns the
    promoted-download entry dict, ``_PAUSED_MATCH`` for a same-key member that
    is queued PAUSED (it can still resume, so the group is not exhausted), or
    ``None`` for a non-match.
    """
    if not isinstance(group, dict) or not _dupekey_match(group, dupe_key):
        return None
    if exclude_nzbid is not None and _same_nzbid(group.get("NZBID"), exclude_nzbid):
        return None
    status = str(group.get("Status") or "")
    if status.upper() == "PAUSED":
        return _PAUSED_MATCH
    downloaded = _as_number(group.get("DownloadedSizeMB"))
    total = _as_number(group.get("FileSizeMB"))
    percent = int(downloaded * 100 / total) if total else 0
    return {
        "present": True,
        "nzbid": group.get("NZBID"),
        "status": status,
        "percent": percent,
    }


def active_group_by_dupekey(dupe_key, exclude_nzbid=None, settings_getter=None):
    """The active (non-PAUSED) queued group sharing ``dupe_key`` (#372 round 2).

    After the pick fails, NZBGet promotes a duplicate backup from history into
    the queue -- it appears in listgroups under the SAME DupeKey with its OWN new
    NZBID and an un-paused status (other backups stay PAUSED). Returns
    a dict with keys ``present``, ``nzbid``, ``status``, and ``percent`` for that
    promoted download, else
    ``{"present": False, "paused_present": <bool>}`` -- ``paused_present`` flags
    a same-key member queued PAUSED (for example, NZBGet globally paused when the
    promotion happened), which the poll must treat as "not exhausted yet"
    rather than a failed group.
    """
    if not dupe_key:
        return {"present": False, "paused_present": False, "paused_nzbids": []}
    groups, error = _rpc_call("listgroups", [0], settings_getter=settings_getter)
    if error is not None or not isinstance(groups, list):
        return {"present": False, "paused_present": False, "paused_nzbids": []}
    paused_nzbids = []
    for group in groups:
        entry = _promoted_group_entry(group, dupe_key, exclude_nzbid)
        if entry is _PAUSED_MATCH:
            # Collect the id: a promotion that landed while NZBGet was paused
            # never becomes the tracked member, but a cancel must still be able
            # to delete it directly (#372 round 5).
            if group.get("NZBID") is not None:
                paused_nzbids.append(group.get("NZBID"))
        elif entry is not None:
            return entry
    return {
        "present": False,
        "paused_present": bool(paused_nzbids),
        "paused_nzbids": paused_nzbids,
    }


def _group_name_match(group, target, exclude_nzbid):
    """One ``listgroups`` row's verdict for the ``active_group_by_name`` scan.

    Split out to keep the caller's cyclomatic complexity down (Codacy
    feedback pattern from PR #406's other extractions).
    """
    if not isinstance(group, dict):
        return False
    if exclude_nzbid is not None and _same_nzbid(group.get("NZBID"), exclude_nzbid):
        return False
    return release_name(group.get("NZBName")).strip().lower() == target


def active_group_by_name(nzb_name, exclude_nzbid=None, settings_getter=None):
    """Whether an active (any status) queued group's name matches ``nzb_name``.

    Guards the #372 r6 FORCE rescue, which has no DupeKey to check on the
    keyless plain submit path (``active_group_by_dupekey("")`` always reports
    absent): before overriding NZBGet's content-fingerprint veto, confirm the
    veto isn't shadowing a FOREIGN download of the same release that's still
    actively queued -- FORCE would otherwise start a wasteful parallel
    download of identical content instead of waiting for/reusing the one
    already in flight. Case-insensitive exact match against ``NZBName``
    (NZBGet's queue display name -- the ``NZBFilename`` sent to ``append``
    minus its ``.nzb`` suffix). Best-effort: an RPC error reports no match
    (fail-open, same as the DupeKey-based promotion scan).
    """
    if not nzb_name:
        return False
    groups, error = _rpc_call("listgroups", [0], settings_getter=settings_getter)
    if error is not None or not isinstance(groups, list):
        return False
    target = str(nzb_name).strip().lower()
    return any(_group_name_match(group, target, exclude_nzbid) for group in groups)


def history_success_by_dupekey(dupe_key, exclude_nzbids=None, settings_getter=None):
    """A SUCCESS history item sharing ``dupe_key`` (a completed group member).

    Lets the poll play a duplicate backup that NZBGet already downloaded to
    success after the pick failed (#372 round 2). ``exclude_nzbids`` filters
    out STALE successes that predate the resolve (#372 round 4): their files
    may be long gone -- the reuse probe already declined them -- and playing
    one would fail "No video file found" instead of waiting for the fleet's
    own member. Returns a dict with keys ``present``, ``nzbid``, ``job_name``,
    and ``dest_dir``, or ``{"present": False}``.
    """
    if not dupe_key:
        return {"present": False}
    hist, error = _rpc_call("history", [False], settings_getter=settings_getter)
    if error is not None or not isinstance(hist, list):
        return {"present": False}
    for item in hist:
        entry = _success_history_entry(item, dupe_key)
        if entry is None or _nzbid_in(entry["nzbid"], exclude_nzbids):
            continue
        return entry
    return {"present": False}


def _nzbid_in(nzbid, nzbids):
    """True when ``nzbid`` matches any id in ``nzbids`` (str/int tolerant)."""
    return any(_same_nzbid(nzbid, other) for other in nzbids or ())


def history_rows(settings_getter=None):
    """NZBGet's history as a list (one RPC), or None when it couldn't be read.

    Lets a caller that needs several facts from history (same-key successes,
    the highest same-key score) read it once; None keeps "unknown" distinct
    from an empty history.
    """
    rows, error = _rpc_call("history", [False], settings_getter=settings_getter)
    return rows if error is None and isinstance(rows, list) else None


def queue_rows(settings_getter=None):
    """NZBGet's queue (``listgroups``) as a list, or None when it couldn't be read."""
    rows, error = _rpc_call("listgroups", [0], settings_getter=settings_getter)
    return rows if error is None and isinstance(rows, list) else None


# Same-key history statuses that make re-sending an NZB pointless: NZBGet
# already refused it as a copy, or it already failed.
_DEAD_MEMBER_PREFIXES = ("FAILURE/", "WARNING/", "DELETED/COPY")


def dupekey_member_states(dupe_key, history, queue):
    """``{nzbid: "queued" | "parked" | "dead"}`` for NZBGet's members of ``dupe_key``.

    ``"queued"``: in the queue (downloading, waiting or paused).

    ``"parked"``: a ``DELETED/DUPE`` history backup NZBGet can still
    promote on a failover -- a working backup, so sending another copy is
    waste. ``"dead"``: a ``FAILURE/*``, ``WARNING/*`` (the resolver's
    terminal failure too) or ``DELETED/COPY`` row -- sending it again would
    fail or be refused again. Anything else (a success, a manual
    delete) is absent: a fresh copy of it is a useful backup. Both lists are
    already-read ``history_rows`` / ``queue_rows``.
    """
    states = {}
    for rows, is_history in ((history or [], True), (queue or [], False)):
        for row in rows:
            if not isinstance(row, dict) or not _dupekey_match(row, dupe_key):
                continue
            status = str(row.get("Status") or "").upper()
            if not is_history:
                state = "queued"
            elif status == "DELETED/DUPE":
                state = "parked"
            elif status.startswith(_DEAD_MEMBER_PREFIXES):
                state = "dead"
            else:
                continue
            nzbid = _int_nzbid(row.get("NZBID"))
            if nzbid is not None:
                states[nzbid] = state
    return states


def _int_nzbid(value):
    """``value`` as an int NZBID (listgroups may send a string), or None."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def config_options(names, settings_getter=None):
    """Several running-config options from ONE ``config`` RPC.

    Returns ``{lowercased name: lowercased value}`` for the requested names
    that exist; ``{}`` when the RPC fails (callers treat it as best-effort).
    """
    rows, error = _rpc_call("config", [], settings_getter=settings_getter)
    if error is not None or not isinstance(rows, list):
        return {}
    wanted = {str(name).lower() for name in names}
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name", "")).lower()
        if name in wanted:
            found[name] = str(row.get("Value", "")).strip().lower()
    return found


def max_dupe_score_by_dupekey(dupe_key, settings_getter=None, history=None, queue=None):
    """Highest DupeScore NZBGet holds for ``dupe_key`` (queue + history), or None.

    A fresh fleet must outrank every same-key item NZBGet already has
    (NZBGet only downloads a SCORE-mode duplicate that beats them), and a
    wall-clock score base cannot promise that after the box's clock rolls
    back. DupeKeys match case-insensitively, like NZBGet's. ``history`` and
    ``queue`` are an already-read ``history_rows`` / ``queue_rows`` (else they
    are fetched). Best-effort: RPC errors are skipped; None when nothing
    matches.
    """
    if not dupe_key:
        return None
    if history is None:
        history = history_rows(settings_getter) or []
    if queue is None:
        queue = queue_rows(settings_getter) or []
    best = None
    for rows in (history, queue):
        for row in rows:
            if not isinstance(row, dict) or not _dupekey_match(row, dupe_key):
                continue
            try:
                score = int(row.get("DupeScore"))
            except (TypeError, ValueError):
                continue
            best = score if best is None else max(best, score)
    return best


def success_ids_by_dupekey(dupe_key, settings_getter=None, history=None):
    """NZBIDs of every SUCCESS history row sharing ``dupe_key`` (#372 round 4).

    Snapshotted when a dupe-enabled poll starts so group-follow can exclude
    successes that PREDATE the resolve (see ``history_success_by_dupekey``).
    ``history`` is an already-read ``history_rows`` (else it is fetched).
    Best-effort: an RPC error or empty history yields ``[]``.
    """
    if not dupe_key:
        return []
    hist = history_rows(settings_getter) if history is None else history
    if hist is None:
        return []
    ids = []
    for item in hist:
        entry = _success_history_entry(item, dupe_key)
        if entry is not None and entry["nzbid"] is not None:
            ids.append(entry["nzbid"])
    return ids


def _success_history_entry(item, dupe_key):
    """Build the completed-member entry for one history row, or None.

    Keeps ``history_success_by_dupekey`` a flat scan: the DupeKey match and
    the SUCCESS/* gate (same completion guarantee as ``history_status``—only
    a fully post-processed row is playable) live here so the caller only
    decides "match or keep scanning."
    """
    if not isinstance(item, dict) or not _dupekey_match(item, dupe_key):
        return None
    if not str(item.get("Status") or "").startswith("SUCCESS"):
        return None
    return {
        "present": True,
        "nzbid": item.get("NZBID"),
        "job_name": release_name(item.get("Name")),
        "dest_dir": _dest_dir(item),
    }


def _final_delete(command, ids, settings_getter):
    """One best-effort ``editqueue`` final-delete for all ``ids`` at once.

    Skips the RPC entirely when the scan matched nothing—an empty-IDs
    editqueue would be a pointless round-trip on every cancel.
    """
    if not ids:
        return True
    result, error = _rpc_call(
        "editqueue", [command, "", ids], settings_getter=settings_getter
    )
    # editqueue answers ``true`` on success; ``false`` (or nothing) is a failure.
    return error is None and result is True
