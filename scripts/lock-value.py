#!/usr/bin/env python3
"""Print one scalar from the repository version lock."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def lookup(value: Any, key_path: str) -> Any:
    for key in key_path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(key_path)
        value = value[key]
    if isinstance(value, (dict, list)):
        raise TypeError(f"{key_path} is not a scalar")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("key_path")
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config/versions.lock.yml",
    )
    args = parser.parse_args()
    try:
        document = yaml.safe_load(args.lock.read_text())
        print(lookup(document, args.key_path))
    except (OSError, KeyError, TypeError, yaml.YAMLError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
