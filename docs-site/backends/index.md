# Backend services

Choose a playback backend in **Settings > Add-ons > My add-ons > Video add-ons > NeNeTeePee-Stream-Kodi > Configure > Playback backend**.

Install and configure your server using its project documentation before you connect the add-on. These guides cover the add-on settings.

| Backend | Playback | Search configuration | Setup guide |
| --- | --- | --- | --- |
| nzbdav / InfiniDysk | Streams through WebDAV and the local proxy. | Configure indexers in the add-on. | [Connect nzbdav or InfiniDysk](nzbdav.md) |
| NZBGet | Downloads and processes the file before playback. | Configure indexers in the add-on. | [Connect NZBGet](nzbget.md) |
| StreamNZB | Plays the HTTP stream supplied by the server. | Configure indexers on StreamNZB. | [Connect StreamNZB](streamnzb.md) |

All three backends use the release picker with parsed filenames, local filters, and sorting. Recovery behavior depends on the backend. See the individual guides for details.

Use an address that Kodi can reach. On the Kodi device, `localhost` refers to that device.
