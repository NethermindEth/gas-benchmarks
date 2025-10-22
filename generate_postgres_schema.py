import psycopg2
import argparse
import sys
import getpass
import logging
from typing import Dict, Any, Optional, List, Tuple

def get_sql_for_benchmark_table(table_name: str) -> str:
    """
    Generates the CREATE TABLE SQL statement for PostgreSQL to store individual benchmark runs,
    associated aggregated statistics, and detailed computer specifications.
    The table name is parameterized.
    """

    # Note: F-string for table_name is generally safe here as it's controlled by our script's argparser,
    # not direct user SQL input for this part. For other parameters, always use query parameterization.
    benchmark_runs_table_sql = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id SERIAL PRIMARY KEY,
    client_name TEXT NOT NULL,
    run_number INTEGER NOT NULL,
    test_title TEXT NOT NULL,
    gas_value TEXT NULL,
    scenario_identifier TEXT NULL,
    payload_status TEXT NULL,
    latest_valid_hash TEXT NULL,
    validation_error TEXT NULL,
    ingestion_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""

    benchmark_metrics_table_sql = """
CREATE TABLE IF NOT EXISTS benchmark_metrics (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES benchmark_runs(id) ON DELETE CASCADE,
    measurement TEXT NOT NULL,
    unit TEXT NULL,
    unit_duration TEXT NULL,
    count INTEGER NULL,
    minimum DOUBLE PRECISION NULL,
    maximum DOUBLE PRECISION NULL,
    mean DOUBLE PRECISION NULL,
    median DOUBLE PRECISION NULL,
    stddev DOUBLE PRECISION NULL,
    p99 DOUBLE PRECISION NULL,
    p95 DOUBLE PRECISION NULL,
    p75 DOUBLE PRECISION NULL,
    total DOUBLE PRECISION NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""
    return benchmark_runs_table_sql + benchmark_metrics_table_sql

def execute_sql_on_db(db_params: Dict[str, Any], table_name: str) -> None:
    """
    Connects to the PostgreSQL database.
    If the specified table does not exist, it creates it.
    If the table exists, it checks for predefined columns and adds them if they are missing.

    Args:
        db_params: Dictionary with database connection parameters.
        table_name: The name of the table to create or alter.
    """
    conn: Optional[psycopg2.extensions.connection] = None
    try:
        logging.info(f"Connecting to PostgreSQL database '{db_params['dbname']}' on {db_params['host']}:{db_params['port']}...")
        conn = psycopg2.connect(**db_params)
        logging.info("Database connection successful.")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'benchmark_runs'
                );
            """)
            runs_exists = cur.fetchone()[0]

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'benchmark_metrics'
                );
            """)
            metrics_exists = cur.fetchone()[0]

            if not runs_exists or not metrics_exists:
                logging.info("Creating benchmark_runs and benchmark_metrics tables.")
                create_sql = get_sql_for_benchmark_table(table_name)
                cur.execute(create_sql)
                logging.info("Tables created successfully.")
            conn.commit()
            logging.info("Database schema for benchmark tables is up to date.")
    except psycopg2.OperationalError as error:
        logging.error(f"Error connecting to PostgreSQL: {error}")
        # No rollback needed if connection itself failed.
        sys.exit(1) # Exit if connection fails
    except (psycopg2.Error, Exception) as error: # Catches other psycopg2 errors or general exceptions
        logging.error(f"Error while executing SQL query: {error}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                logging.info("Database transaction rolled back due to error.")
            except psycopg2.Error as rb_error:
                logging.error(f"Error during rollback attempt: {rb_error}")
        sys.exit(1) # Exit on execution error
    finally:
        if conn:
            conn.close()
            logging.info("PostgreSQL connection closed.")

if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    parser = argparse.ArgumentParser(
        description="Generate and execute PostgreSQL CREATE TABLE statement for benchmark data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--db-host", required=True, help="PostgreSQL database host.")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL database port (default: 5432).")
    parser.add_argument("--db-user", required=True, help="PostgreSQL database user.")
    parser.add_argument("--db-name", required=True, help="PostgreSQL database name.")
    parser.add_argument("--table-name", default="benchmark_data", help="Name for the table to be created (default: benchmark_data).")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )

    args = parser.parse_args()

    # Update logging level based on CLI argument
    logging.getLogger().setLevel(args.log_level.upper())

    db_password: Optional[str] = None
    try:
        # For interactive password entry.
        # In automated CI/CD, prefer environment variables or other secure secret injection.
        # However, we need to run the script only once, so we can use getpass.
        db_password = getpass.getpass(prompt=f"Enter password for PostgreSQL user '{args.db_user}' (or press Enter if handled by .pgpass/env var): ")
        if not db_password: # If user just presses enter, rely on other auth methods
            logging.info("No password entered directly; assuming .pgpass or environment variable for authentication.")
            # psycopg2 will use .pgpass or env vars if password is None or empty string
            # However, to be explicit and avoid sending empty string if getpass returns it for no input:
            db_password = None
    except Exception as error: # Includes EOFError if input stream is not interactive
        logging.warning(f"Could not get password interactively (e.g., running in a non-interactive environment): {error}. ")
        logging.warning("Attempting connection without explicit password, relying on .pgpass or environment variables.")
        db_password = None # Ensure password is None

    db_params: Dict[str, Any] = {
        "host": args.db_host,
        "port": args.db_port,
        "user": args.db_user,
        "dbname": args.db_name,
    }
    if db_password: # Only add password to params if it was actually provided
        db_params["password"] = db_password

    execute_sql_on_db(db_params, args.table_name)
