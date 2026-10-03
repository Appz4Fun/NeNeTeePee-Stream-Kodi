# Connect StreamNZB

!!! note "Current source and next release"
    The unified **Playback backend** section and StreamNZB support are not in
    Stable 1.2.3 or Beta 2.0.0-beta.2. These instructions and illustrations
    describe current source builds and the next release.

    In released builds, configure nzbdav and WebDAV under **Connection**.
    Beta 2.0.0-beta.2 has a separate **NZBGet** section with an
    **Use NZBGet instead of nzbdav for playback** toggle. Stable 1.2.3 supports nzbdav only.
    StreamNZB requires a current source build until a release includes it.


Use [StreamNZB](https://github.com/Gaisberg/streamnzb) for server installation and configuration instructions. Start with a running server that Kodi can reach.

![Example add-on settings for streamnzb](../images/backend-streamnzb.svg)

The illustration shows example values, not a live configuration.

## Configure the add-on

1. Open **Playback backend** in the add-on settings.
2. Select **StreamNZB**.
3. Enter the server base URL that Kodi can reach.
4. Enter your stream token. Use the stream token rather than dashboard administrator credentials.
5. Save the settings.
6. Select a movie or episode in TMDBHelper and choose the add-on player.
7. Select a release in the full release picker.

The add-on parses release filenames and applies your configured filters and sorting. Use the picker’s show-all option to view filtered entries.

StreamNZB manages searches, NZB retrieval, archive streaming, and server recovery. The add-on sends the selected HTTP playback URL and request headers directly to Kodi. Local indexer settings, WebDAV settings, and proxy fallback workers don't apply. Both the API address and the playback address returned by the server must be reachable from Kodi.

See [StreamNZB behavior and identity requirements](../features/streamnzb-backend.md) for supported entry points and recovery limits.

## Archive limits

StreamNZB streams supported RAR and 7z archives whose media is stored without compression. This backend can't stream compressed RAR and 7z releases; they require downloading and unpacking with a download client such as NZBGet or SABnzbd. See [StreamNZB’s supported formats](https://github.com/Gaisberg/streamnzb#what-can-and-cannot-be-streamed) for the upstream format restrictions.
