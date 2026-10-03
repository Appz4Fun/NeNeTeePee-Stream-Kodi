# Playback backend

Choose the service that resolves playback. The selected backend controls which connection fields Kodi shows. nzbdav / InfiniDysk submits NZBs through a SABnzbd-compatible API and streams over WebDAV. NZBGet downloads and unpacks the release before Kodi reads the completed file. StreamNZB searches on its server and returns an HTTP playback URL. Its playback bypasses the local proxy and local indexer clients. See the [backend setup guides](../backends/index.md) for connection examples.

## Kodi screenshots

![Playback backend, view 1](../images/kodi/backend-nzbdav.png)

![Playback backend, view 2](../images/kodi/backend-nzbget.png)

![Playback backend, view 3](../images/kodi/backend-streamnzb.png)

Captured on the OrbStack Kodi VM from commit `db07f61`. Debug overlays are off, and credentials use example values. [Capture details](index.md#capture-details).

## Every option

### Options

| Option and setting ID | Default | What it does |
| --- | --- | --- |
| Playback backend[^source-playback_backend]<br>`playback_backend` | nzbdav / InfiniDysk | Choose the server that searches or resolves playback. Existing NZBGet selections are migrated automatically. Choices: 0 = nzbdav / InfiniDysk, 1 = NZBGet, 2 = StreamNZB. |
| StreamNZB server/base URL[^source-streamnzb_url]<br>`streamnzb_url` | Empty | Use an HTTP or HTTPS base URL reachable from Kodi. Localhost refers to the Kodi device. Visible when playback_backend = 2. |
| StreamNZB stream token[^source-streamnzb_token]<br>`streamnzb_token` | Empty | Use a StreamNZB stream token from its Install page. This field is masked. Do not enter the dashboard administrator password. Kodi uses the token to authorize searches and playback URLs. Visible when playback_backend = 2. |
| nzbdav URL[^source-nzbdav_url]<br>`nzbdav_url` | http://localhost:3000 | Base URL of your nzbdav server (SABnzbd-compatible API). Used to submit and poll downloads. Visible when playback_backend = 0. |
| API Key[^source-nzbdav_api_key]<br>`nzbdav_api_key` | Empty | nzbdav API key, from Settings > Usenet > API Key in nzbdav. Visible when playback_backend = 0. |
| Test nzbdav Connection[^source-action_test_nzbdav]<br>`action_test_nzbdav` | Button | Verify the nzbdav URL and API key are reachable and valid. Visible when playback_backend = 0. |
| WebDAV URL (leave empty to use nzbdav URL)[^source-webdav_url]<br>`webdav_url` | http://localhost:8080 | Base URL of the WebDAV server used to stream files. Leave empty to reuse the nzbdav URL. Visible when playback_backend = 0. |
| Username[^source-webdav_username]<br>`webdav_username` | Empty | WebDAV username, from Settings > WebDAV in nzbdav. Visible when playback_backend = 0. |
| Password[^source-webdav_password]<br>`webdav_password` | Empty | WebDAV password. The dialog masks the value; the profile settings file stores it. Visible when playback_backend = 0. |
| Test WebDAV Connection[^source-action_test_webdav]<br>`action_test_webdav` | Button | Verify WebDAV is reachable with the current URL and credentials. Visible when playback_backend = 0. |
| NZBGet URL[^source-nzbget_url]<br>`nzbget_url` | http://localhost:6789 | NZBGet control address, for example http://host:6789. Visible when playback_backend = 1. |
| NZBGet Username[^source-nzbget_username]<br>`nzbget_username` | nzbget | NZBGet control username. Visible when playback_backend = 1. |
| NZBGet Password[^source-nzbget_password]<br>`nzbget_password` | Empty | NZBGet control password. The dialog masks the value; the profile settings file stores it. Visible when playback_backend = 1. |
| NZBGet Category[^source-nzbget_category]<br>`nzbget_category` | Empty | Category to submit downloads under. Also used to help locate the completed file. Visible when playback_backend = 1. |
| Test NZBGet Connection[^source-action_test_nzbget]<br>`action_test_nzbget` | Button | Verify NZBGet is reachable with the current URL and credentials. Visible when playback_backend = 1. |
| Completed Folder (SMB or Local Path)[^source-nzbget_smb_root]<br>`nzbget_smb_root` | Empty | smb:// URL or local/mounted path of NZBGet's completed-downloads base, used to play the finished file. Visible when playback_backend = 1. |
| Test Completed Folder[^source-action_test_nzbget_smb]<br>`action_test_nzbget_smb` | Button | Verify the completed-downloads folder (SMB share or local path) is reachable. Visible when playback_backend = 1. |

## Source notes

Labels and schema defaults are from commit [`db07f61`](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml). Runtime limits can differ from the editable schema; the explanations identify those limits. Links use the full commit ID, so later changes to `main` do not move them.

[^source-playback_backend]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L7); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/playback_backend.py#L23).
[^source-streamnzb_url]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L17); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/streamnzb_player.py#L111).
[^source-streamnzb_token]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L24); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/streamnzb_player.py#L106).
[^source-nzbdav_url]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L31); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/fallback_streams_probe.py#L313).
[^source-nzbdav_api_key]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L44); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbdav_api.py#L81).
[^source-action_test_nzbdav]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L58).
[^source-webdav_url]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L68); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/fallback_streams_probe.py#L312).
[^source-webdav_username]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L81); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/webdav.py#L92).
[^source-webdav_password]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L94); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/webdav.py#L93).
[^source-action_test_webdav]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L108).
[^source-nzbget_url]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L118); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbget_api.py#L37).
[^source-nzbget_username]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L131); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbget_api.py#L38).
[^source-nzbget_password]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L144); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbget_api.py#L39).
[^source-nzbget_category]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L158); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbget_api.py#L40).
[^source-action_test_nzbget]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L171).
[^source-nzbget_smb_root]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L181); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/nzbget_resolver.py#L430).
[^source-action_test_nzbget_smb]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L194); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/router_conn.py#L203).
