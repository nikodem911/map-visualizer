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

The script uses the Python standard library for both filtering and snapping. To leave the virtual environment when finished, run `deactivate`.

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
python3 location_history_to_kml.py .tmp/timeline_august_2026.json august-car-snapped.kml --car --snap --name "August 2026 driving"
```

`--snap` is a conservative local smoothing pass: it only nudges a point if it is very close to the line between its neighbors, so the route stays faithful to the original track while removing small measurement noise.

The `--car` option selects vehicle activities; `--bike` selects bicycle activities. In `rawSignals` exports, timestamped activity records classify nearby GPS samples. In `semanticSegments` exports, matching activity segments contribute their start and end coordinates.

Import the resulting `.kml` file into a new map at [Google My Maps](https://www.google.com/mymaps): choose **Add layer** or **Import**, then select the KML file.

## Run tests

With the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```