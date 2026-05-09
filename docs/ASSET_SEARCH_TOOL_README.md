# Asset Search & Download Tool — TrippleEffect

A TrippleEffect tool that gives agents the ability to **search and download
open-source, royalty-free assets** across seven public APIs — covering sounds,
3D models, icons, photos/vectors, and PBR textures.

---

## Quick Install

### 1. Copy the tool file

```bash
cp asset_search_tool.py TrippleEffect/src/tools/asset_search_tool.py
```

### 2. Install dependencies

Add to `requirements.txt` (if not already present):

```
aiofiles
```

Then install:

```bash
pip install aiofiles
```

### 3. Configure API keys

Add the following to your `.env` file (copy from `.env.example`):

```env
# Asset Search Tool
FREESOUND_API_KEY=       # https://freesound.org/apiv2/apply/ (free)
SKETCHFAB_API_KEY=       # https://sketchfab.com/settings/password (free)
UNSPLASH_ACCESS_KEY=     # https://unsplash.com/developers (free, 50 req/hr)
PIXABAY_API_KEY=         # https://pixabay.com/api/docs/ (free)
```

**Sources that need no API key:** Poly Pizza, Iconify, PolyHaven.

#### 3.1 Freesound
The tool only uses the search and retrieve endpoints (text search, sound info, preview/download) — these are "read-only" public API operations that authenticate with just a token (your API key) in the query string. OAuth2 / client_id is only needed if you want to access user-specific resources (like a user's profile, uploads, or bookmarks) or if you want users to log in through your app. So:

✅ The tool will work with just FREESOUND_API_KEY
❌ You don't need the client_id for this tool
The callback URL doesn't matter here — that's only for the OAuth2 flow your app will never trigger

#### 3.2 Unsplash
The Access Key is the one used for API requests (it goes in the Authorization: Client-ID <access_key> header). The Secret Key is only needed for OAuth2 user authentication flows (same story as Freesound — for accessing user-specific data). The Application ID is just a reference number for your dashboard.
So:

✅ Only UNSPLASH_ACCESS_KEY is needed — that's the Access Key from your app dashboard
❌ You don't need the Secret Key or Application ID in .env

### 4. Restart TrippleEffect

```bash
./run.sh
```

The `ToolExecutor` discovers tools automatically on startup.

---

## Supported Sources

| Source     | Category | API Key?                   | License        |
|------------|----------|----------------------------|----------------|
| freesound  | sounds   | Yes — `FREESOUND_API_KEY`  | CC (varies)    |
| polypizza  | 3d       | No                         | CC0            |
| sketchfab  | 3d       | Optional — `SKETCHFAB_API_KEY` | CC0 (filtered) |
| iconify    | icons    | No                         | Open-source    |
| unsplash   | images   | Yes — `UNSPLASH_ACCESS_KEY`| Unsplash License|
| pixabay    | images   | Yes — `PIXABAY_API_KEY`    | Pixabay License|
| polyhaven  | textures | No                         | CC0            |

---

## Actions

### `search`

Search one or more sources for a keyword.

| Parameter  | Type    | Required | Default | Notes                                              |
|------------|---------|----------|---------|----------------------------------------------------|
| `query`    | string  | ✅        | —       | Search keyword(s)                                  |
| `category` | string  | ❌        | `all`   | `sounds`, `3d`, `icons`, `images`, `textures`, `all` |
| `source`   | string  | ❌        | —       | Specific source; overrides `category`              |
| `limit`    | integer | ❌        | `5`     | Results per source, 1–20                           |

**Native JSON example:**
```json
{
  "action": "search",
  "query": "forest ambience",
  "category": "sounds",
  "limit": 3
}
```

**XML fallback example:**
```xml
<asset_search>
  <action>search</action>
  <query>forest ambience</query>
  <category>sounds</category>
  <limit>3</limit>
</asset_search>
```

**Sample output:**
```
=== FREESOUND ===
[1] Forest - Morning Birds
    URL      : https://freesound.org/people/.../sounds/123456/
    Preview  : https://cdn.freesound.org/previews/123/123456_hq.mp3
    License  : https://creativecommons.org/licenses/by/4.0/
    Tags     : forest, birds, morning, nature, ambience, field-recording
    Download : https://freesound.org/apiv2/sounds/123456/download/?token=...
```

---

### `download`

Save an asset directly to the shared workspace `assets/` folder.

| Parameter      | Type   | Required | Notes                                    |
|----------------|--------|----------|------------------------------------------|
| `download_url` | string | ✅        | Direct file URL from a prior search result |
| `filename`     | string | ✅        | Target filename, e.g. `explosion.mp3`    |

```xml
<asset_search>
  <action>download</action>
  <download_url>https://freesound.org/apiv2/sounds/123456/download/?token=KEY</download_url>
  <filename>forest_ambience.mp3</filename>
</asset_search>
```

Files are saved to:
- **With project/session context:** `workspace/<project>/<session>/assets/<filename>`
- **Without context:** `<agent_sandbox>/assets/<filename>`

---

### `list_sources`

Returns a summary table of all sources with their categories and key requirements.

```xml
<asset_search>
  <action>list_sources</action>
</asset_search>
```

---

## Modular Help

Agents can request per-action documentation to save context tokens:

```xml
<tool_information>
  <action>get_info</action>
  <tool_name>asset_search</tool_name>
  <sub_action>search</sub_action>
</tool_information>
```

Valid `sub_action` values: `search`, `download`, `list_sources`.

---

## Example Agent Workflow

A typical multi-step asset workflow an agent might execute:

1. **Discover sources:**
   ```xml
   <asset_search><action>list_sources</action></asset_search>
   ```

2. **Search for a 3D tree model:**
   ```xml
   <asset_search>
     <action>search</action>
     <query>oak tree low poly</query>
     <category>3d</category>
     <limit>5</limit>
   </asset_search>
   ```

3. **Download the best result:**
   ```xml
   <asset_search>
     <action>download</action>
     <download_url>https://api.poly.pizza/v1/download/abc123.glb</download_url>
     <filename>oak_tree.glb</filename>
   </asset_search>
   ```

4. **Fetch a matching ambient sound:**
   ```xml
   <asset_search>
     <action>search</action>
     <query>wind through leaves</query>
     <source>freesound</source>
     <limit>3</limit>
   </asset_search>
   ```

---

## Notes

- **Rate limits:** Unsplash free tier allows 50 requests/hour. Freesound and Pixabay
  are more generous. Iconify and PolyHaven have no documented limits.
- **Sketchfab downloads** require OAuth authentication for actual file downloads;
  this tool returns the viewer URL for reference. Use the Sketchfab web UI or their
  OAuth flow to download files directly.
- **License verification:** Always confirm the specific license of a downloaded asset
  before use in commercial projects. CC0 sources (Poly Pizza, PolyHaven) are the
  safest for unrestricted use.
