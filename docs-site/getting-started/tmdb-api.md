# Set up the TMDB API key

The optional **TMDB API key (optional, movies and TV)** field helps the add-on search by the identifiers an indexer accepts. It converts a TMDB movie ID to an IMDb ID and a TMDB TV series ID to a TVDB ID when the player has not already supplied that identifier. It is not a Usenet provider key or a TVDB API key.[^lookup]

## Get a key

1. Create or sign in to your [TMDB account](https://www.themoviedb.org/).
2. Open your account **Settings → API** and request a developer API key. Follow TMDB's application instructions. TMDB documents this route in its [API FAQ](https://developer.themoviedb.org/docs/faq#how-do-i-apply-for-an-api-key).
3. Copy the **API Key**, often described as the API v3 key. The add-on expects this key, not the longer **API Read Access Token**.
4. Open **NeNeTeePee-Stream-Kodi → Configure → Indexers**. Scroll to **TV search accuracy** and paste the key into **TMDB API key (optional, movies and TV)**.
5. Select **OK** to save. See [phone copy and paste](phone-remote.md) for an easier way to enter it.

![The TMDB API key field at the bottom of Indexers](../images/kodi/indexers-09.png)

## What happens without a key

The add-on still uses identifiers supplied by TMDBHelper. When conversion is unavailable or a lookup fails, it falls back to the supplied IDs or title text. A key improves identity matching; it does not guarantee that an indexer has a matching release. StreamNZB owns its own metadata and search configuration, so this local field does not configure its server.[^lookup]

TMDBHelper is a separate add-on. Any API configuration in its own settings belongs to TMDBHelper, not this field. Install and connect [TMDBHelper](tmdbhelper.md) to browse titles and launch this player.

[^lookup]: [TMDB setting and help at the captured commit](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6/repo/plugin.video.nzbdav/resources/settings.xml#L673). See the per-setting [runtime reference](../settings/indexers.md#source-notes) for the lookup implementation.
