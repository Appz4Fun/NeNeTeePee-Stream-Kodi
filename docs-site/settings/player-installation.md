# Player Installation

TMDBHelper is required for the title-browsing workflow documented here. These buttons install a player definition; they do not install TMDBHelper or a backend service. Run the installation again after updating the add-on. The installer preserves a player file with the current schema and backs up an older file before replacing it. See [TMDBHelper setup](../getting-started/tmdbhelper.md).

## Kodi screenshots

![Player Installation, view 1](../images/kodi/player-installation-01.png)

Captured on the OrbStack Kodi VM from commit `db07f61`. Debug overlays are off, and credentials use example values. [Capture details](index.md#capture-details).

## Every option

### Options

| Option and setting ID | Default | What it does |
| --- | --- | --- |
| Install TMDBHelper Player[^source-action_install_player]<br>`action_install_player` | Button | Install the NeNeTeePee-Stream-Kodi player file into TMDBHelper. |
| Install Player Other[^source-action_install_player_other]<br>`action_install_player_other` | Button | Install the NeNeTeePee-Stream-Kodi player file into another add-on that has a players folder. |

## Source notes

Labels and schema defaults are from commit [`db07f61`](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml). Runtime limits can differ from the editable schema; the explanations identify those limits. Links use the full commit ID, so later changes to `main` do not move them.

[^source-action_install_player]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L688); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/player_installer.py#L267).
[^source-action_install_player_other]: [Setting declaration](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L695); [runtime reference](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/lib/player_installer.py#L273).
