import argparse
import csv
import shutil
from pathlib import Path
from typing import Set

from gas_benchmarks.merge import merge_csv, merge_html


def collect_filenames(*directories: Path) -> Set[str]:
    names: Set[str] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file():
                names.add(path.name)
    return names


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def merge_files(first: Path, second: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if first.suffix.lower() == ".csv":
        with first.open("r", encoding="utf-8", newline="") as fh:
            first_data = list(csv.reader(fh))
        with second.open("r", encoding="utf-8", newline="") as fh:
            second_data = list(csv.reader(fh))
        merged = merge_csv(first_data, second_data)
        with output.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerows(merged)
    elif first.name == "index.html":
        with first.open("r", encoding="utf-8") as fh:
            first_html = fh.read()
        with second.open("r", encoding="utf-8") as fh:
            second_html = fh.read()
        output.write_text(merge_html(first_html, second_html), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported file type for merge: {first.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge benchmarking report artefacts")
    parser.add_argument("--first", type=Path, default=Path("merge5/"), help="First reports directory")
    parser.add_argument("--second", type=Path, default=Path("reports8/"), help="Second reports directory")
    parser.add_argument("--output", type=Path, default=Path("merge6/"), help="Output directory")
    args = parser.parse_args()

    first_dir: Path = args.first
    second_dir: Path = args.second
    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    file_names = collect_filenames(first_dir, second_dir)

    for name in sorted(file_names):
        first_file = first_dir / name
        second_file = second_dir / name
        destination = output_dir / name

        if first_file.exists() and not second_file.exists():
            copy_file(first_file, destination)
        elif second_file.exists() and not first_file.exists():
            copy_file(second_file, destination)
        elif first_file.exists() and second_file.exists():
            try:
                merge_files(first_file, second_file, destination)
            except ValueError as exc:
                print(f"[WARN] {exc}. Copying first file instead.")
                copy_file(first_file, destination)
        else:
            print(f"[WARN] Neither input contains {name}")

    print(f"Merged reports written to {output_dir}")


if __name__ == "__main__":
    main()

