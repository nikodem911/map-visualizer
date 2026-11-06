#!/usr/bin/env python3

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def count_coordinates(kml_path: Path) -> int:
    root = ET.parse(kml_path).getroot()
    return sum(
        len((element.text or "").split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "coordinates"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Count coordinate tuples in a KML file.")
    parser.add_argument("kml_file", type=Path, help="Path to the KML file")
    args = parser.parse_args()
    print(count_coordinates(args.kml_file))


if __name__ == "__main__":
    main()