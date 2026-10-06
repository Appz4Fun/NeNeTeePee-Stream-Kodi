# TMDbHelper playback identity handoff

The `executebuiltin://RunScript` player route bypasses TMDbHelper's normal
PlayerInfoString publication. A bare final Kodi ListItem also lacks the media
type and canonical IDs needed by its monitor. The addon now supplies both at
its final playback boundaries; TMDbHelper remains the sole Trakt scrobbler.

## Behavior

Canonical movie/show/episode identity is captured before selecting a release.
Release filenames, URLs, resume keys, and backend job identities remain separate.
Final VideoInfoTag fields and Home-window `TMDbHelper.PlayerInfoString` are
prepared before playback. Unknown or unusable identity clears stale context.
Series IDs use `tvshow.tmdb`; episode IDs use `tmdb`. StreamNZB
`type=series` inputs normalize to canonical episode metadata.

| Backend/path | Final boundary | Regression coverage |
| --- | --- | --- |
| NZB-DAV / InfiniDysk | Direct, proxy and MP4 faststart; handle and handle-less | `test_playback_handoff_paths.py` |
| NZBGet | Fresh completion and completed-file reuse; both entry modes | `test_playback_handoff_paths.py` |
| StreamNZB | Its own player and handle completion; resume | `test_streamnzb.py` |
| Diagnostic direct playback | Proxy handoff | `test_playback_router_context.py` |
| Service reconnect | Session-local metadata and selected offset | `test_playback_handoff_paths.py` |
| Routing | Canonical identity survives release selection | `test_playback_router_context.py`, `test_router.py` |

Resume/start-over properties remain on the same decorated item. StreamNZB
retains its server-owned retry/failover and does not gain addon retry workers.
A service retry canceled or replaced during its delay cannot republish an old
session's identity. Fresh playback reserves a session token before publishing
context. A process lock covers metadata publication through the native playback
call, so a concurrent reconnect cannot replace the next item's identity. Service
snapshots and cleanup respect that reservation.

## Automatic player upgrade

Existing TMDbHelper `nzbdav.json` players migrate to schema 10 at addon-service
startup or fresh addon entry. Users do not need to reinstall the player from
settings after an addon update. The migration replaces owned routing fields,
backs up the original, and preserves custom name, priority and unrelated fields.
Current/newer schemas and foreign players remain untouched. Missing players are
not installed automatically. Malformed files and write/backup failures leave
the original in place and log a local diagnostic. Migration is silent and does
not change TMDbHelper settings or authentication.

Backups are `nzbdav.<unique-id>.bak` beside the existing player. Automatic
migration uses a process lock and validated atomic replacement. The manual
installer remains available. It has its existing separate settings behavior.

## Consumer contract and limitations

Fixtures in `tests/fixtures/tmdbhelper_6_17_5_*.txt` contain selected verbatim
methods captured from installed TMDbHelper 6.17.5 on October 6, 2026, with source
hashes and attribution. Tests execute its monitor/scrobbler gates, property
namespace and dictionary expansion using fake Kodi/API boundaries.

Predestination's expected final tags are movie, Predestination, 2014, TMDb
206487 and IMDb tt2397535. Its context is:

```json
{"tmdb_type":"movie","tmdb_id":"206487","imdb_id":"tt2397535"}
```

TMDbHelper 6.17.5 rejects zero-season episodes for scrobbling. Season 0 metadata
is preserved, but unusable scrobble context is cleared. This repair does not
modify TMDbHelper or submit independent Trakt requests. MDBList authentication
errors remain a separate service issue.

## Provenance and delivery evidence

Earlier local commit `390cd2b` held an unintegrated repair. Current `main` at
`43b63d6` lacked it; that commit was not in fetched `origin/main`. The ZIP builder
already includes addon Python recursively. This change adapts the repair to
current code and adds StreamNZB and automatic migration coverage. Packaging
regressions verify the real ZIP contains the tested helper and boundary sources.

Automated tests prove the metadata/API contract, not a live watched-history
entry. Deployment must back up the addon and active player, preserve active
playback, and avoid restarting Kodi. Installed hashes, loaded service behavior,
actual Trakt requests, and independently read history are separate gates.
A code PR does not itself publish an addon release.

Validation of the implementation before PR review: `just lint`, `just test`
(3,228 passed, 4 skipped), `just compat-3-8`, whitespace checks and `just release`
passed. Native deployment and real Trakt watched-history verification are
reported separately; they are not established by these checks.
