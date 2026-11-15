# Set up TMDBHelper

Beta 2.0.0-beta.3 and later use the Kodi menu name **NeNeTeePee-Stream-Kodi**. Stable 1.2.3 still uses **NZB-DAV**. In Stable, choose that name wherever these instructions show **NeNeTeePee-Stream-Kodi**.

[TMDBHelper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) is required for this title-browsing workflow. NeNeTeePee-Stream-Kodi plays titles you choose in TMDBHelper. To connect the two, you
install a NeNeTeePee-Stream-Kodi *player file* into TMDBHelper and set it as your default
player.

## Install the player file

1. Make sure TMDBHelper is installed and you've
   [configured NeNeTeePee-Stream-Kodi's connections](configuration.md).
2. In NeNeTeePee-Stream-Kodi settings, open the **Player Installation** tab.
3. Select **Install TMDBHelper Player**.

This writes a small `nzbdav.json` player file into TMDBHelper's players folder
(`addon_data/plugin.video.themoviedb.helper/players/`) and registers
**NeNeTeePee-Stream-Kodi** as a selectable playback source. It also turns on TMDBHelper's
`only_resolve_strm` setting. Without that setting, TMDBHelper doesn't run
NeNeTeePee-Stream-Kodi's script action directly (see [Why NeNeTeePee-Stream-Kodi uses a script player](#why-neneteepee-stream-kodi-uses-a-script-player)).
You get a **Player installed to: TMDBHelper** notification.

Where TMDBHelper has no default player chosen yet, the install also makes
**NeNeTeePee-Stream-Kodi** the default for movies and for episodes. A default
you already chose, for any player, stays as it is. The one exception is a
default set to a player the helper generated for this add-on
(`autogen.plugin.video.nzbdav.json`): that player can't play, so the install
replaces it.

NeNeTeePee-Stream-Kodi protects your data while doing this:

- It refuses to write anywhere outside Kodi's add-on data folder.
- If a player file with the same schema version is already present, NeNeTeePee-Stream-Kodi
  keeps it as is, so your manual edits survive. NeNeTeePee-Stream-Kodi backs up a file from an
  older schema version to `nzbdav.bak` and then replaces it. If it can't write
  the backup, the install stops and your existing file stays in place.
- If the write fails, you get a **Failed to install to: …** message instead of
  a false success.

!!! tip "The player file follows add-on updates"
    When an update changes the player file, the add-on updates an installed
    `nzbdav.json` on the next Kodi start and keeps the name and priority you
    set in that file. You don't need to install it again. A copy you changed
    with the helper's **Configure players** screen is saved separately
    (`reconfigured_players/nzbdav.json`) and is not updated; delete that copy
    after an update that changes the player.

![Player Installation settings](../images/kodi/player-installation-01.png)

See [every player installation option](../settings/player-installation.md) for commit-pinned installer references.

### Skins with their own TMDBHelper

Some skins install their own copy of TMDBHelper and open titles through it.
The Bingie skin does this with **TMDb Bingie Helper**
(`plugin.video.tmdb.bingie.helper`). That copy has its own players folder and
its own default player, so it doesn't see a player installed only into
TMDBHelper.

**Install TMDBHelper Player** covers both. When TMDb Bingie Helper is
installed, the same button also writes `nzbdav.json` into its players folder
(`addon_data/plugin.video.tmdb.bingie.helper/players/`, created when missing),
turns on its `only_resolve_strm` setting, and makes NeNeTeePee-Stream-Kodi its
default player where none is chosen. You get a second notification,
**Player installed to: TMDb Bingie Helper**.

If you add such a skin later, you don't have to come back here for the player
file: once the player is installed in one of the two, the add-on copies it to
the other on the next Kodi start. That automatic copy writes the file only.
Select **Install TMDBHelper Player** once more to also set the default player
and `only_resolve_strm` there. A player file left behind by a helper you
uninstalled is never copied from.

!!! warning "Don't use the helper's own \"create player\" for this add-on"
    TMDBHelper and its forks can generate a player for any add-on from a
    template. A player made that way for NeNeTeePee-Stream-Kodi sends only the
    title, as a movie search, so an episode opens a picker with every season
    and episode of the show. Use **Install TMDBHelper Player** instead, and
    remove the generated file
    (`reconfigured_players/autogen.plugin.video.nzbdav.json` in the helper's
    add-on data folder).

### Install into another add-on's player list

If you use a different front end that reads TMDBHelper-style player files,
select **Install Player Other** on the same tab. NeNeTeePee-Stream-Kodi scans Kodi's add-on
data folders (`addon_data/*/players/`) for add-ons that already have a
`players` folder, apart from TMDBHelper and NeNeTeePee-Stream-Kodi itself. It lists them and
installs `nzbdav.json` into the one you choose, with the same safeguards as
preceding. There's no fixed list of supported add-ons. If no add-on has a
`players` folder yet, you get a notification and nothing is written. This
route doesn't change any setting in the other add-on.

## Set NeNeTeePee-Stream-Kodi as your default player

1. Restart Kodi, **or** open TMDBHelper and select **Players → Update players**.
2. The install already made **NeNeTeePee-Stream-Kodi** the default where no
   default player was chosen. If you had another default, open TMDBHelper
   settings and set **Default player (Movies)** and **Default player
   (TV Shows)** to **NeNeTeePee-Stream-Kodi**. A skin's own TMDBHelper has the
   same two settings in its own settings screen.

The add-on's [optional TMDB API key](tmdb-api.md) belongs to its Indexers settings. It is separate from the player selection and TMDBHelper's own API settings.

## Why NeNeTeePee-Stream-Kodi uses a script player

The NeNeTeePee-Stream-Kodi player file launches playback with a `RunScript` action instead of a
`plugin://` URL. This is deliberate: on CoreELEC and Kodi 21, asking Kodi to
open a `plugin://` URL as a playable item can crash the player before NeNeTeePee-Stream-Kodi's
code even runs. `RunScript` enters the add-on directly, shows the source picker,
and then starts playback. You don't need to
configure anything for this; the installed player file already does it.

## Verify

Open any movie or episode in TMDBHelper and start playback. If the NeNeTeePee-Stream-Kodi
source picker appears, setup is complete. If **NeNeTeePee-Stream-Kodi** doesn't show up as a
player, see
[Troubleshooting → NeNeTeePee-Stream-Kodi doesn't appear in TMDBHelper](../operations/troubleshooting.md).

## Next step

[Play your first title](first-playback.md).

## Refresh the player name

If TMDBHelper still shows the old add-on name, run the player installation action again. The installer backs up the previous player file before replacing it with the renamed player. The add-on ID and playback routes stay the same.
