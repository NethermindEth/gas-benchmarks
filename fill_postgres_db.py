import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psycopg2
from psycopg2 import sql

from utils import read_results

INTERESTED_PREFIXES = ("engine_newpayload", "engine_forkchoiceupdated")
SUMMARY_FIELDS = {
    "sum": "total",
    "min": "minimum",
    "max": "maximum",
    "mean": "mean",
    "median": "median",
    "stddev": "stddev",
    "p99": "p99",
    "p95": "p95",
    "p75": "p75",
}
COUNT_FIELDS = ("count.hist", "count")
EXCLUDED_TEST_SUBSTRINGS = ("setup", "cleanup", "warmup")


def get_db_connection(params: Dict[str, Any]) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(**params)
    logging.info(
        "Connected to database '%s' on %s:%s", params["dbname"], params["host"], params["port"]
    )
    return conn


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_run_metadata(path: Path) -> Optional[Dict[str, Any]]:
    stem = path.stem  # remove .txt
    match = re.match(r"^(?P<client>[^_]+)_results_(?P<run>\d+)_(?P<rest>.+)$", stem)
    if not match:
        logging.warning("Skipping file with unexpected name format: %s", path.name)
        return None

    client = match.group("client")
    run_number = int(match.group("run"))
    remainder = match.group("rest")

    if "-gas-value_" not in remainder:
        logging.warning("Missing gas value in filename: %s", path.name)
        return None

    test_part, gas_value = remainder.rsplit("-gas-value_", 1)
    test_title = test_part

    if any(keyword in test_title.lower() for keyword in EXCLUDED_TEST_SUBSTRINGS):
        logging.info("Skipping non-testing scenario: %s", test_title)
        return None

    return {
        "client": client,
        "run_number": run_number,
        "test_title": test_title,
        "gas_value": gas_value,
        "scenario_identifier": test_part,
    }


def parse_response_file(path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    status: Optional[str] = None
    latest_valid_hash: Optional[str] = None
    validation_error: Optional[str] = None

    if not path.exists():
        logging.warning("Response file not found: %s", path)
        return status, latest_valid_hash, validation_error

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            logging.debug("Skipping invalid JSON line in %s", path)
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            continue
        if "payloadStatus" in result:
            ps = result.get("payloadStatus", {})
            status = ps.get("status", status)
            latest_valid_hash = ps.get("latestValidHash", latest_valid_hash)
            validation_error = ps.get("validationError", validation_error)
        else:
            status = result.get("status", status)
            latest_valid_hash = result.get("latestValidHash", latest_valid_hash)
            validation_error = result.get("validationError", validation_error)

    return status, latest_valid_hash, validation_error


def measurement_is_relevant(measurement: str) -> bool:
    m_lower = measurement.lower()
    return any(prefix in m_lower for prefix in INTERESTED_PREFIXES)


def parse_results_file(path: Path) -> List[Dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    sections = read_results(content)
    metrics: List[Dict[str, Any]] = []

    for section in sections.values():
        measurement = section.measurement or ""
        measurement = measurement.replace("[Application]", "").strip()
        if not measurement_is_relevant(measurement):
            continue

        entry: Dict[str, Any] = {
            "measurement": measurement,
            "unit": section.tags.get("unit"),
            "unit_duration": section.tags.get("unit_dur"),
            "count": None,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "median": None,
            "stddev": None,
            "p99": None,
            "p95": None,
            "p75": None,
            "total": None,
        }

        for raw_key, mapped_key in SUMMARY_FIELDS.items():
            entry[mapped_key] = _to_float(section.fields.get(raw_key))

        for count_key in COUNT_FIELDS:
            count_val = section.fields.get(count_key)
            if count_val not in (None, ""):
                try:
                    entry["count"] = int(float(count_val))
                except ValueError:
                    entry["count"] = None
                break

        metrics.append(entry)

    return metrics


def insert_run(cursor: psycopg2.extensions.cursor, run: Dict[str, Any], response: Tuple[Optional[str], Optional[str], Optional[str]]) -> int:
    status, latest_valid_hash, validation_error = response
    cursor.execute(
        """
        INSERT INTO benchmark_runs
            (client_name, run_number, test_title, gas_value, scenario_identifier, payload_status, latest_valid_hash, validation_error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """,
        (
            run["client"],
            run["run_number"],
            run["test_title"],
            run["gas_value"],
            run["scenario_identifier"],
            status,
            latest_valid_hash,
            validation_error,
        ),
    )
    return cursor.fetchone()[0]


def insert_metrics(cursor: psycopg2.extensions.cursor, run_id: int, metrics: Iterable[Dict[str, Any]]) -> None:
    for metric in metrics:
        cursor.execute(
            """
            INSERT INTO benchmark_metrics
                (run_id, measurement, unit, unit_duration, count, minimum, maximum, mean, median, stddev, p99, p95, p75, total)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                run_id,
                metric["measurement"],
                metric["unit"],
                metric["unit_duration"],
                metric["count"],
                metric["minimum"],
                metric["maximum"],
                metric["mean"],
                metric["median"],
                metric["stddev"],
                metric["p99"],
                metric["p95"],
                metric["p75"],
                metric["total"],
            ),
        )


def collect_runs(results_root: Path) -> List[Tuple[Dict[str, Any], Path, Path]]:
    runs: List[Tuple[Dict[str, Any], Path, Path]] = []
    for results_path in sorted(results_root.glob("*_results_*.txt")):
        metadata = extract_run_metadata(results_path)
        if not metadata:
            continue
        response_path = results_path.with_name(results_path.name.replace("_results_", "_response_"))
        runs.append((metadata, results_path, response_path))
    return runs


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Populate PostgreSQL with benchmark metrics extracted directly from raw Kute results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--results-dir", required=True, help="Path containing *_results_*.txt files.")
    parser.add_argument("--db-host", required=True)
    parser.add_argument("--db-port", type=int, default=5432)
    parser.add_argument("--db-user", required=True)
    parser.add_argument("--db-password", required=True)
    parser.add_argument("--db-name", required=True)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity.",
    )

    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level.upper())

    results_root = Path(args.results_dir).resolve()
    if not results_root.exists():
        logging.error("Results directory does not exist: %s", results_root)
        sys.exit(1)

    runs = collect_runs(results_root)
    if not runs:
        logging.warning("No results files found under %s", results_root)
        sys.exit(0)

    conn = get_db_connection(
        {
            "host": args.db_host,
            "port": args.db_port,
            "user": args.db_user,
            "password": args.db_password,
            "dbname": args.db_name,
        }
    )

    inserted_runs = 0
    inserted_metrics = 0

    try:
        with conn:
            with conn.cursor() as cursor:
                for metadata, results_path, response_path in runs:
                    metrics = parse_results_file(results_path)
                    if not metrics:
                        logging.debug("No relevant metrics found in %s", results_path.name)
                        continue

                    response_data = parse_response_file(response_path)
                    cursor.execute(
                        """
                        DELETE FROM benchmark_runs
                        WHERE client_name = %s AND run_number = %s AND test_title = %s AND gas_value = %s;
                        """,
                        (
                            metadata["client"],
                            metadata["run_number"],
                            metadata["test_title"],
                            metadata["gas_value"],
                        ),
                    )

                    run_id = insert_run(cursor, metadata, response_data)
                    insert_metrics(cursor, run_id, metrics)

                    inserted_runs += 1
                    inserted_metrics += len(metrics)

        conn.commit()
    finally:
        conn.close()

    logging.info("Inserted %d runs and %d metric rows.", inserted_runs, inserted_metrics)


if __name__ == "__main__":
    main()
