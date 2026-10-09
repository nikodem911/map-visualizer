# Google Timeline to KML

Export selected car or bicycle activity samples from Google Takeout Location History JSON as KML for Google My Maps. By default, the script uses the recorded GPS positions directly. It supports exports containing `rawSignals` with timestamped `activityRecord` and `position` entries, and exports containing `semanticSegments` with classified activities and their start/end coordinates.

## Requirements

- Python 3.10 or newer

## Create and activate a virtual environment

From this directory, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project dependencies into the activated virtual environment:

```bash
python -m pip install -r requirements.txt
```

This installs Mappymatch and its GIS dependencies for OSM road matching. The `local` snapping strategy itself uses only the Python standard library. To leave the virtual environment when finished, run `deactivate`.

## Export for Google My Maps

Choose an activity mode. No API key is needed to export the recorded positions:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car.kml --car --name "August 2026 driving"
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-bike.kml --bike --name "August 2026 cycling"
```

You can limit the export to a specific day or a date range with `-d/--date`:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-2026-08-12.kml --car --date 2026-08-12 --name "August 12 driving"
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-aug-2026.kml --car --date 2026-08-01:2026-08-10 --name "August 2026 driving"
```

The date filter uses the calendar date written in each timestamp, regardless of its time of day or timezone offset. Both endpoints of a date range are included.

To smooth only minor GPS jitter without needing any external API, run the export with `--snap`.

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-snapped.kml --car --snap --snap-strategy local --name "August 2026 driving"
```

`--snap-strategy` (`-S`) selects the strategy used by `--snap`. It defaults to `local`. The local strategy smooths only minor GPS jitter, splits a route at time gaps or physically impossible jumps rather than drawing a line across them, and merges road sections already covered by earlier trips: waypoints within 35 meters of an earlier trace (in either direction) are pulled onto that trace so repeated trips render as a single coincident line, and any edge still at least 70% covered afterward is suppressed entirely. It recognizes travel in either direction; nearby parallel roads farther apart than the tolerance remain separate. It does not download map data or match points against a road network. The optional `mappymatch` strategy matches traces against locally cached OpenStreetMap driving roads.

To match routes to local OpenStreetMap road data with Mappymatch, select the `mappymatch` strategy:

```bash
python3 location_history_to_kml.py .tmp/timeline.json august-car-matched.kml --car --date 2024-08-01:2024-08-31 --snap -S mappymatch --name "August 2024 driving"
```

To use OSRM's Match service, select `osrm`:

```bash
python3 location_history_to_kml.py .tmp/timeline.json august-car-osrm.kml --car --snap -S osrm --name "August 2024 driving"
```

This sends GPS coordinates and available timestamps to `https://router.project-osrm.org` and uses the returned matched road geometries. The public service is rate-limited and supports only profiles enabled by its operator. Use `--osrm-url` for another OSRM server and `--osrm-profile` to select a profile it provides; the defaults are `driving` for car and `cycling` for bike. Long runs are sent in overlapping batches of at most 50 points; if a server reports its trace limit was exceeded, the request is retried with smaller batches.

### Use a Local Overpass Docker Server

To avoid relying on the public Overpass service, you can run a regional Overpass instance locally with the community [`wiktorn/overpass-api`](https://hub.docker.com/r/wiktorn/overpass-api) image. This example initializes a persistent server from a Geofabrik Poland extract, bound to localhost only. Change the extract URLs and volume name to a region that contains all the trips you intend to match.

```bash
mkdir -p "$HOME/.local/share/overpass-poland"
docker run -d \
	--name overpass-local \
	-p 127.0.0.1:12345:80 \
	-v "$HOME/.local/share/overpass-poland:/db" \
	-e OVERPASS_MODE=init \
	-e OVERPASS_META=no \
	-e OVERPASS_PLANET_URL=https://download.geofabrik.de/europe/poland-latest.osm.pbf \
	-e 'OVERPASS_PLANET_PREPROCESS=mv /db/planet.osm.bz2 /db/planet.osm.pbf && osmium cat -o /db/planet.osm.bz2 /db/planet.osm.pbf && rm /db/planet.osm.pbf' \
	-e OVERPASS_DIFF_URL=https://download.geofabrik.de/europe/poland-updates/ \
	wiktorn/overpass-api:v0.7.62.9
```

The first run downloads and imports the extract. Follow initialization with `docker logs -f overpass-local`. When initialization finishes the container exits; start it again to serve queries and apply updates:

```bash
docker start overpass-local
docker logs -f overpass-local
```

Check that the local API responds:

```bash
curl -G --data-urlencode 'data=[out:json];node(52.0,19.0,52.001,19.001);out;' http://127.0.0.1:12345/api/interpreter
```

Pass the Overpass base URL to the exporter (use `/api`, not `/api/interpreter`):

```bash
python3 location_history_to_kml.py .tmp/timeline.json august-car-local-osm.kml --car --date 2024-08-01:2024-08-31 --snap -S mappymatch --overpass-url http://127.0.0.1:12345/api --name "August 2024 driving"
```

The database persists in `$HOME/.local/share/overpass-poland`; stop and resume it with `docker stop overpass-local` and `docker start overpass-local`. The Poland extract only covers Poland, so all matched route sections must be within that extract. For cross-border trips, choose a Geofabrik extract that covers every route or merge the required regional extracts before import. The Europe PBF is about 33 GB compressed and requires substantially more disk after import; smaller regional extracts use less. See the [Overpass image documentation](https://hub.docker.com/r/wiktorn/overpass-api) and [Geofabrik downloads](https://download.geofabrik.de/) for current usage and extract information.

The `--car` option selects vehicle activities; `--bike` selects bicycle activities. In `rawSignals` exports, timestamped activity records classify nearby GPS samples. In `semanticSegments` exports, matching activity segments include their start/end coordinates and any timestamped `timelinePath` samples during the trip.

Import the resulting `.kml` file into a new map at [Google My Maps](https://www.google.com/mymaps): choose **Add layer** or **Import**, then select the KML file.

## Run tests

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```