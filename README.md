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

The project requires only the standard library for the built-in `local` strategy, plus the OSRM client dependencies used by the `osrm` strategy. To leave the virtual environment when finished, run `deactivate`.

## Export for Google My Maps

Choose an activity mode. No API key is needed to export the recorded positions:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car.kml --car --name "August 2026 driving"
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-bike.kml --bike --name "August 2026 cycling"
```

Set the route line color with `-C/--color`. Available colors are `orange` (default), `blue`, `green`, `red`, `purple`, `yellow`, `white`, and `black`:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-blue.kml --car --color blue --name "August 2026 driving"
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

`--snap-strategy` (`-S`) selects the strategy used by `--snap`. It defaults to `local`. The local strategy smooths only minor GPS jitter, splits a route at time gaps or physically impossible jumps rather than drawing a line across them, and merges road sections already covered by earlier trips: waypoints within 35 meters of an earlier trace (in either direction) are pulled onto that trace so repeated trips render as a single coincident line, and any edge still at least 70% covered afterward is suppressed entirely. It recognizes travel in either direction; nearby parallel roads farther apart than the tolerance remain separate. It does not download map data or match points against a road network.

To use OSRM's Match service, select `osrm`:

```bash
python3 location_history_to_kml.py .tmp/timeline.json august-car-osrm.kml --car --snap -S osrm --name "August 2024 driving"
```

This sends GPS coordinates and available timestamps to `https://router.project-osrm.org` and uses the returned matched road geometries. The public service is rate-limited and supports only profiles enabled by its operator. Use `--osrm-url` for another OSRM server and `--osrm-profile` to select a profile it provides; the defaults are `driving` for car and `cycling` for bike. Long runs are sent in overlapping batches of at most 50 points; if a server reports its trace limit was exceeded, the request is retried with smaller batches.

### Use a Local OSRM Docker Server

OSRM needs a preprocessed OpenStreetMap extract. This example uses the Poland extract; replace it with a Geofabrik extract that covers your routes. The initial download and preprocessing can take a while and require several times the PBF file size in free disk space.

```bash
mkdir -p "$HOME/.local/share/osrm-poland"
cd "$HOME/.local/share/osrm-poland"
wget -O region.osm.pbf https://download.geofabrik.de/europe/poland-latest.osm.pbf

docker run --rm -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
	osrm-extract -p /opt/car.lua /data/region.osm.pbf
docker run --rm -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
	osrm-partition /data/region.osrm
docker run --rm -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \
	osrm-customize /data/region.osrm
```

Start the local HTTP server using the generated dataset:

```bash
docker run -d \
	--name osrm-local \
	--restart unless-stopped \
	-p 127.0.0.1:5000:5000 \
	-v "$HOME/.local/share/osrm-poland:/data" \
	ghcr.io/project-osrm/osrm-backend \
	osrm-routed --algorithm mld /data/region.osrm
```

Check that the Match service responds with a route in the downloaded region:

```bash
curl 'http://127.0.0.1:5000/match/v1/driving/21.0122,52.2297;21.0130,52.2300?geometries=geojson&overview=simplified'
```

Point the exporter at the local server:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-local-osrm.kml --car --date 2026-08-01 --snap -S osrm --osrm-url http://127.0.0.1:5000 --name "August 2026 local OSRM driving"
```

The container serves car routing data only because it was preprocessed with `car.lua`. For bicycle matching, preprocess a separate dataset with OSRM's bicycle profile, run another server on a different port, and set `--osrm-profile cycling` if needed by that server. View logs with `docker logs -f osrm-local`; stop and remove the server with `docker stop osrm-local && docker rm osrm-local`. The data remains in `$HOME/.local/share/osrm-poland`. See the [official OSRM Docker instructions](https://github.com/Project-OSRM/osrm-backend#using-docker) and [Geofabrik downloads](https://download.geofabrik.de/) for image usage and regional extracts.

The `--car` option selects vehicle activities; `--bike` selects bicycle activities. In `rawSignals` exports, timestamped activity records classify nearby GPS samples. In `semanticSegments` exports, matching activity segments include their start/end coordinates and any timestamped `timelinePath` samples during the trip.

Import the resulting `.kml` file into a new map at [Google My Maps](https://www.google.com/mymaps): choose **Add layer** or **Import**, then select the KML file.

## Run tests

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```