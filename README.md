# Google Timeline to KML

Export selected car or bicycle activity samples from Google Takeout Location History JSON as KML for Google My Maps. By default, the script uses the recorded GPS positions directly. It supports exports containing `rawSignals` with timestamped `activityRecord` and `position` entries.

## Requirements

- Python 3.10 or newer

## Create and activate a virtual environment

From this directory, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

The script uses the Python standard library for both filtering and snapping. To leave the virtual environment when finished, run `deactivate`.

## Export for Google My Maps

Choose an activity mode. No API key is needed to export the recorded positions:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car.kml --car --name "August 2026 driving"
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-bike.kml --bike --name "August 2026 cycling"
```

To smooth only minor GPS jitter without needing any external API, run the export with `--snap`.

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-snapped.kml --car --snap --name "August 2026 driving"
```

`--snap` is a conservative local smoothing pass: it only nudges a point if it is very close to the line between its neighbors, so the route stays faithful to the original track while removing small measurement noise.

The `--car` option selects `IN_ROAD_VEHICLE` and `IN_VEHICLE`; `--bike` selects `ON_BICYCLE`. Timestamped activity records classify nearby GPS samples. Snapping batches up to 100 recorded points per request.

Import the resulting `.kml` file into a new map at [Google My Maps](https://www.google.com/mymaps): choose **Add layer** or **Import**, then select the KML file.

## Run tests

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```