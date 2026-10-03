# StreamNZB backend

[StreamNZB](https://github.com/Gaisberg/streamnzb) can search and stream releases
through the existing NeNeTeePee-Stream-Kodi TMDBHelper player. No additional Kodi add-on or
Jellyfin server is required.

## Configuration

1. Open NeNeTeePee-Stream-Kodi settings and the **Playback backend** tab.
2. Select **StreamNZB** in the **Playback backend** dropdown. Its URL and token
   fields appear below the dropdown on the same tab. There is no separate
   Connection tab. The local **Indexers** settings do not apply to StreamNZB;
   configure its indexers on the StreamNZB server.
3. Enter the **StreamNZB server/base URL**, for example
   `http://192.168.1.100:7000`. Use only the server address and any reverse-proxy
   prefix, without a token or `manifest.json` suffix.
4. Enter the **StreamNZB stream token**. In StreamNZB's dashboard, open the
   existing stream's settings and copy its token. You can also extract it from
   that stream's Stremio manifest URL: `http://server:7000/TOKEN/manifest.json`.
   Use the stream token, not dashboard administrator credentials. The field is masked;
   do not share manifest or playback URLs, which contain the token. NeNeTeePee-Stream-Kodi
   redacts its diagnostics; Kodi itself must receive the credential-bearing URL
   and may include it in native networking/debug logs. Sanitize those before
   sharing them.
5. Select a movie or episode in TMDBHelper and choose the existing NeNeTeePee-Stream-Kodi
   player. The full NZB picker shows release filenames and parsed metadata,
   using the add-on's configured filters and sorting. Select a release or
   cancel the picker.

The server URL **and the playback URLs it advertises must be reachable from
Kodi**. `localhost` on Kodi refers to the Kodi device. If the server advertises
an unreachable hostname, correct StreamNZB's advertised base URL in its own
configuration. Prefer HTTPS when accessing a server outside a trusted network.

For Kodi and StreamNZB hosted in the same OrbStack VM, use the VM's reachable
hostname (for example `http://kodi-vm.orb.local:7000`) and configure StreamNZB
to advertise that same address. Publish the container's port to the VM. Kodi
still needs access to StreamNZB's returned playback address, even if the API
request uses a different address.

The dropdown also selects **nzbdav / InfiniDysk** or **NZBGet**. Existing NZBGet
users retain their backend through a one-time migration from the old toggle.
The default for new installations remains nzbdav. The former NZBGet toggle is
no longer shown.

## Search and playback ownership

StreamNZB owns indexer searches, filtering, ranking, NZB retrieval, archive
handling and server-side failover. NeNeTeePee-Stream-Kodi applies its local release filters
and sorting to the returned entries and hands the selected HTTP playback URL
directly to Kodi, preserving query parameters
and supplied request headers. It does not submit an NZB, discover WebDAV files,
run a remux/proxy or start NeNeTeePee-Stream-Kodi fallback workers for this backend.

NZBHydra2, Prowlarr, direct indexers, nzbdav, WebDAV, NZBGet, and a local TMDB key
are not required in NeNeTeePee-Stream-Kodi's settings for StreamNZB. Configure the needed
indexers, metadata services, and Usenet providers on StreamNZB instead. Local
NeNeTeePee-Stream-Kodi filters and sorting apply to the shared release picker, including its
show-all option. Auto-selection, completed-download tags, season-pack reuse
and fallback settings do not apply to StreamNZB playback. Only directly
playable HTTP/HTTPS entries are listed; informational `externalUrl`, torrent
and unsupported entries are omitted.

Requests use `/{token}/stream/movie/{id}.json` for movies and
`/{token}/stream/series/{show-id}:{season}:{episode}.json` for episodes (path
components are URL encoded). IDs can be IMDb `tt…`, `tmdb:…` or `tvdb:…`.
TMDB/TVDB-only support depends on StreamNZB's configured metadata services.
No title-only search is performed if identifiers are missing.

Episodes use the **show** identifier. NeNeTeePee-Stream-Kodi retains TMDBHelper's existing
`season`/`episode` precedence over `ep_season`/`ep_episode` aliases, including
season-zero specials and episode zero. When numbers are missing, the existing
focused-item recovery is used only when the show title and every supplied
season/episode coordinate match the focused item. Otherwise an
error asks for the missing identity or numbers. Anime exposed by TMDBHelper as
show episodes uses those canonical numbers; no local Kitsu mapping or absolute
number conversion is added. StreamNZB owns its own anime metadata mappings.
The installed player uses TMDBHelper's `{tmdb}` placeholder for the show ID;
`{tmdb_id}` can identify the individual episode. Reinstall the TMDBHelper
player from NeNeTeePee-Stream-Kodi after updating to receive that corrected template.

Search requests have a finite 35-second timeout. Kodi shutdown is checked
before and after the request and before playback. No background request worker
is started; an in-flight request can take until its timeout to finish. The
non-modal progress indicator closes on every outcome and before the picker.
Handle-less cancellations and failures notify; plugin requests always complete
with a resolved URL or a failure signal.

## Routes and recovery limits

Captured Kodi/TMDBHelper resume bookmarks use Kodi's resume-or-restart choice.
The selected offset is passed on the playable ListItem. Cancelling preserves
the consumed bookmark under a token-free movie or episode key for the next
attempt. This does not start NeNeTeePee-Stream-Kodi's playback retry monitor.

Supported identity-based paths are TMDBHelper `RunScript(...,tmdb_play,...)`,
`/play`, `/search`, `/resolve`, and `resolve_and_play` when content identity is
provided. A manual title-only search cannot identify a StreamNZB title and
reports that it needs an ID. `/resolve-v2` carries an NZB source manifest rather
than content identity; it reports the missing identity when StreamNZB is
selected. `/direct_play` retains its separate diagnostic proxy contract and
is not a StreamNZB backend entry point.

StreamNZB may rebuild a failed playback slot with another NZB when the player
reconnects, or redirect to a different release's slot. Kodi's reconnect and
redirect behavior determines the outcome. **Seamless Kodi failover has not been
verified**. No second independent recovery system is added. HTTP Range requests
go directly to StreamNZB, so seeking and recovery require validation with the
server and Kodi version in use.

With StreamNZB v6.2.0 and Kodi 21.3 in an OrbStack Ubuntu ARM64 VM, a selected
movie played through `/play` and an episode played through TMDBHelper's
RunScript player. Both advanced after seeking to one minute. Small byte-range
checks returned HTTP 206 at the start and at a 1 MiB offset. Picker cancellation
closed the dialog; handle-less cancellation displayed a notification. These
checks establish selection, ordinary playback and seeking for those releases;
they do not establish midstream recovery or seamless failover.

The API contract was inspected at reference snapshot
[`242a9c5`](https://github.com/Gaisberg/streamnzb/tree/242a9c5dd5a411ab8bec76418b0b52e1a0ca07f4)
and the deployed v6.2.0 source
[`c5aa001`](https://github.com/Gaisberg/streamnzb/tree/c5aa001b0051c0f866768e30c97292c7d1a6c216).
Same-release recovery changes in
[`aa90a69`](https://github.com/Gaisberg/streamnzb/commit/aa90a69a036100eb81d322a47f8cf4c3fa5d07a9)
do not by themselves prove seamless recovery in Kodi.

## NZBHydra2 as the only StreamNZB indexer

One Hydra Newznab endpoint can supply multiple different releases, but its
standard `/api` search suppresses duplicate-group members before StreamNZB
receives the response. StreamNZB keeps returned same-release NZBs as variants;
it cannot recover copies that Hydra omitted. **All copies** in StreamNZB means
all copies it has received, not every copy behind Hydra.

Hydra v9.0.4 has a source-derived workaround: in its advanced Searching
settings, set **Duplicate detection → Duplicate age threshold** to **-1**.
Its duplicate comparison requires a nonnegative age difference to be less
than or equal to that threshold, so a negative threshold prevents grouping.
Zero still groups results with matching ages. This changes duplicate detection
for all Hydra clients. A live v9.0.4 check after this change returned all 142
rows reported for a test search, including 20 same-title groups with distinct
NZB links. That confirms duplicates in that response, not every possible
result from upstream indexers. The add-on does not change Hydra settings.

Set StreamNZB's **All copies** option for same-release attempts. Allow enough
time for Hydra to query its indexers: the default five-second Newznab timeout
can produce empty StreamNZB responses while Hydra is still searching. The
OrbStack test deployment uses a 20-second Hydra indexer timeout. If indexers
are defined by container environment variables, those override dashboard
changes; move the indexer definition into saved configuration to retain its
timeout setting.

The same Hydra-only OrbStack deployment recorded 94 candidates grouped into 21
movie release choices with 73 additional variants retained, and 15 episode
candidates grouped into seven choices with eight additional variants. These
are StreamNZB's saved search diagnostics, separate from testing whether Kodi
reconnects after a failed copy.

Other approaches are configuring individual indexers directly in StreamNZB or
adding an integration with Hydra's internal search API that returns duplicate
group members. The Kodi backend does not change Hydra settings or add that
integration. Different-release fallback remains owned by StreamNZB and Kodi
reconnect behavior still needs live testing.
