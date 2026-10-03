# Backend services

!!! note "Current source and next release"
    The unified **Playback backend** section and StreamNZB support are not in
    Stable 1.2.3 or Beta 2.0.0-beta.2. These instructions and screenshots
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

## Backend projects

Use each project's documentation to install and configure its service. The guides on this site cover connecting Kodi to an existing service.

| Project | Role |
| --- | --- |
| [nzbdav](https://github.com/nzbdav-dev/nzbdav) | SABnzbd-compatible NZB submission and WebDAV streaming |
| [InfiniDysk](https://www.infinidysk.com/) | An alternative service using the same Kodi nzbdav / InfiniDysk backend selection |
| [StreamNZB](https://github.com/Gaisberg/streamnzb) | Server-managed search, archive streaming, and recovery |
| [NZBGet](https://github.com/nzbgetcom/nzbget) | Download and unpack before Kodi reads the completed file |
| [xbmc4lyfe NZBGet fork and Debian builds](https://github.com/xbmc4lyfe/nzbget/tree/fork-ci) | Optional fork builds; consult the branch and its build artifacts |
| [NZBGet duplicate-article recovery proposal, PR #850](https://github.com/nzbgetcom/nzbget/pull/850) | Source and review history for the fork's recovery work |

The fork is optional. The Kodi NZBGet backend uses the configured server's JSON-RPC API and completed-folder path; selecting NZBGet does not install a fork or guarantee that its recovery features are present.

[TMDBHelper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) is required for the documented title-browsing workflow. Connect it with the [player installation guide](../getting-started/tmdbhelper.md).

The [original NZBGet repository](https://github.com/nzbget/nzbget) is archived. Use the maintained nzbgetcom project for current installation instructions and packages.
