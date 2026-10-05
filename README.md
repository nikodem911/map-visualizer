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

The script uses only the Python standard library. To leave the virtual environment when finished, run `deactivate`.

## Export for Google My Maps

Choose an activity mode. No API key is needed to export the recorded positions:

```bash
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car.kml --car --name "August 2026 driving"
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-bike.kml --bike --name "August 2026 cycling"
```

To snap those GPS samples to roads, enable the Google Roads API and billing in a Google Cloud project, create a key restricted to that API, then set it in the environment. `--snap` sends the selected coordinates to Google's Snap to Roads endpoint, which may interpolate road geometry between samples. With sparse points, the inferred road path may be inaccurate. Requests may incur charges.

```bash
read -rsp "Google Maps API key: " GOOGLE_MAPS_API_KEY
echo
export GOOGLE_MAPS_API_KEY
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-snapped.kml --car --snap --name "August 2026 driving"
```

The `--car` option selects `IN_ROAD_VEHICLE` and `IN_VEHICLE`; `--bike` selects `ON_BICYCLE`. Timestamped activity records classify nearby GPS samples. Snapping batches up to 100 recorded points per request.

Import the resulting `.kml` file into a new map at [Google My Maps](https://www.google.com/mymaps): choose **Add layer** or **Import**, then select the KML file.

Only `--snap` sends selected coordinates to Google. Review the privacy implications before enabling it.

## Run tests

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```