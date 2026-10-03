# Backend services

!!! note "Current source and next release"
    The unified **Playback backend** section and StreamNZB support are not in
    Stable 1.2.3 or Beta 2.0.0-beta.2. These instructions and illustrations
    describe current source builds and the next release.

    In released builds, configure nzbdav and WebDAV under **Connection**.
    Beta 2.0.0-beta.2 has a separate **NZBGet** section with an
    **Use NZBGet instead of nzbdav for playback** toggle. Stable 1.2.3 supports nzbdav only.
    StreamNZB requires a current source build until a release includes it.


Current released packages (Stable 1.2.3 and Beta 2.0.0-beta.2) use the Kodi menu name **NZB-DAV**. In those builds, choose that name wherever these instructions show **NeNeTeePee-Stream-Kodi**. Renamed source builds use the new name.

Choose a playback backend in **Settings > Add-ons > My add-ons > Video add-ons > NeNeTeePee-Stream-Kodi > Configure > Playback backend**.

Install and configure your server using its project documentation before you connect the add-on. These guides cover the add-on settings.

| Backend | Playback | Search configuration | Setup guide |
| --- | --- | --- | --- |
| nzbdav / InfiniDysk | Streams through WebDAV and the local proxy. | Configure indexers in the add-on. | [Connect nzbdav or InfiniDysk](nzbdav.md) |
| NZBGet | Downloads and processes the file before playback. | Configure indexers in the add-on. | [Connect NZBGet](nzbget.md) |
| StreamNZB | Plays the HTTP stream supplied by the server. | Configure indexers on StreamNZB. | [Connect StreamNZB](streamnzb.md) |

All three backends use the release picker with parsed filenames, local filters, and sorting. Recovery behavior depends on the backend. See the individual guides for details.

Use an address that Kodi can reach. On the Kodi device, `localhost` refers to that device.
