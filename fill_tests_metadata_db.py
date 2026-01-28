#!/usr/bin/env python3
"""
Script to populate PostgreSQL database with opcode metrics from a JSON file.

The JSON file is expected to have the structure:
{
    "test_name": {
        "OPCODE1": count,
        "OPCODE2": count,
        ...
    },
    ...
}

Creates a single table with test name and opcodes as JSONB.
"""

import psycopg2
from psycopg2.extras import Json
import json
import os
import argparse
import sys
import logging
from typing import Any, Dict, List, Optional


# --- Database Schema ---
def get_create_table_sql(table_name: str) -> str:
    """
    Returns SQL statement to create the required table.

    Args:
        table_name: Name of the table to create.

    Returns:
        SQL statement to execute.
    """
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY,
            test_name TEXT UNIQUE NOT NULL,
            opcodes JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """


def get_create_indexes_sql(table_name: str) -> List[str]:
    """
    Returns SQL statements to create indexes for efficient querying.

    Args:
        table_name: Name of the table.

    Returns:
        List of SQL statements.
    """
    return [
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_test_name ON {table_name}(test_name)",
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_opcodes ON {table_name} USING GIN (opcodes)",
    ]


# --- Database Interaction ---
def get_db_connection(db_params: Dict[str, Any]) -> Optional[psycopg2.extensions.connection]:
    """
    Establishes a connection to the PostgreSQL database.

    Args:
        db_params: A dictionary containing database connection parameters
                   (host, port, user, password, dbname).

    Returns:
        A psycopg2 connection object if successful, None otherwise.
    """
    conn = None
    try:
        conn = psycopg2.connect(**db_params)
        logging.info(f"Successfully connected to database '{db_params['dbname']}' on {db_params['host']}.")
    except psycopg2.OperationalError as error:
        logging.error(f"Error connecting to PostgreSQL: {error}")
        return None
    except Exception as error:
        logging.error(f"An unexpected error occurred during database connection: {error}")
        return None
    return conn


def create_table_if_not_exist(cursor: psycopg2.extensions.cursor, table_name: str) -> None:
    """
    Creates the required table if it doesn't exist.

    Args:
        cursor: Database cursor.
        table_name: Name of the table.
    """
    try:
        cursor.execute(get_create_table_sql(table_name))
        logging.debug(f"Table {table_name} created or already exists.")

        for sql in get_create_indexes_sql(table_name):
            cursor.execute(sql)
            logging.debug(f"Index created: {sql[:50]}...")
    except psycopg2.Error as error:
        logging.error(f"Error creating table: {error}")
        raise


def load_json_file(file_path: str) -> Dict[str, Dict[str, int]]:
    """
    Loads and parses the JSON file containing opcode metrics.

    Args:
        file_path: Path to the JSON file.

    Returns:
        Dictionary mapping test names to opcode counts.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"JSON file not found: {file_path}")

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    logging.info(f"Loaded {len(data)} tests from {file_path}")
    return data


def populate_metrics_data(
    cursor: psycopg2.extensions.cursor,
    table_name: str,
    metrics_data: Dict[str, Dict[str, int]]
) -> int:
    """
    Populates the database with metrics data.

    Args:
        cursor: Database cursor.
        table_name: Name of the table.
        metrics_data: Dictionary mapping test names to opcode counts.

    Returns:
        Number of records inserted/updated.
    """
    records_processed = 0

    for test_name, opcodes in metrics_data.items():
        try:
            cursor.execute(
                f"""
                INSERT INTO {table_name} (test_name, opcodes)
                VALUES (%s, %s)
                ON CONFLICT (test_name) DO UPDATE SET
                    opcodes = EXCLUDED.opcodes,
                    updated_at = NOW()
                """,
                (test_name, Json(opcodes))
            )
            records_processed += 1

            if records_processed % 100 == 0:
                logging.debug(f"Processed {records_processed} records...")

        except psycopg2.Error as error:
            logging.error(f"Error inserting test '{test_name}': {error}")
            raise

    return records_processed


def clear_existing_data(cursor: psycopg2.extensions.cursor, table_name: str) -> None:
    """
    Clears existing data from the table (useful for re-imports).

    Args:
        cursor: Database cursor.
        table_name: Name of the table.
    """
    cursor.execute(f"TRUNCATE TABLE {table_name}")
    logging.info(f"Cleared existing data from {table_name}")


# --- Main Execution ---
def main() -> None:
    """
    Main function to parse arguments, connect to DB, and populate metrics data.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    parser = argparse.ArgumentParser(
        description="Parse opcode metrics JSON and populate PostgreSQL database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--json-file",
        required=True,
        help="Path to the JSON file containing opcode metrics."
    )
    parser.add_argument(
        "--db-host",
        required=True,
        help="PostgreSQL database host."
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=5432,
        help="PostgreSQL database port."
    )
    parser.add_argument(
        "--db-user",
        required=True,
        help="PostgreSQL database user."
    )
    parser.add_argument(
        "--db-password",
        required=True,
        help="PostgreSQL database password."
    )
    parser.add_argument(
        "--db-name",
        required=True,
        help="PostgreSQL database name."
    )
    parser.add_argument(
        "--table-name",
        default="test_metadata",
        help="Name of the table to store metrics."
    )
    parser.add_argument(
        "--clear-existing",
        action="store_true",
        help="Clear existing data before importing (truncates table)."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )

    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level.upper())

    db_params: Dict[str, Any] = {
        "host": args.db_host,
        "port": args.db_port,
        "user": args.db_user,
        "password": args.db_password,
        "dbname": args.db_name,
    }

    # Load JSON file first (fail fast if file is missing/invalid)
    try:
        metrics_data = load_json_file(args.json_file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"Failed to load JSON file: {e}")
        sys.exit(1)

    conn = get_db_connection(db_params)
    if not conn:
        logging.critical("Failed to establish database connection. Exiting.")
        sys.exit(1)

    try:
        with conn.cursor() as cursor:
            # Create table if it doesn't exist
            logging.info(f"Ensuring table exists: {args.table_name}")
            create_table_if_not_exist(cursor, args.table_name)
            conn.commit()

            # Optionally clear existing data
            if args.clear_existing:
                clear_existing_data(cursor, args.table_name)
                conn.commit()

            # Populate data
            logging.info("Populating metrics data...")
            records_inserted = populate_metrics_data(cursor, args.table_name, metrics_data)

            conn.commit()
            logging.info(f"Successfully committed all changes. Records processed: {records_inserted}")

    except (psycopg2.Error, Exception) as e:
        logging.error(f"An error occurred during database operations: {e}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                logging.info("Database transaction rolled back.")
            except psycopg2.Error as rb_error:
                logging.error(f"Error during rollback: {rb_error}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()
            logging.info("PostgreSQL connection closed.")


if __name__ == "__main__":
    main()
