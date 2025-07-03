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
    benchmark_data_table_sql = f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    id SERIAL PRIMARY KEY,
    client_name TEXT NOT NULL,
    ingestion_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    client_version TEXT NULL,

    -- Test case identification and aggregated stats (primarily from output_{"{client}"}.csv)
    test_title TEXT,                 -- Corresponds to 'Title' in output_*.csv and 'Test Case' in raw_*.csv
    max_mgas_s REAL NULL,          -- Aggregated Max from output_*.csv
    p50_mgas_s REAL NULL,          -- Aggregated p50 from output_*.csv
    p95_mgas_s REAL NULL,          -- Aggregated p95 from output_*.csv
    p99_mgas_s REAL NULL,          -- Aggregated p99 from output_*.csv
    min_mgas_s REAL NULL,          -- Aggregated Min from output_*.csv
    n_samples INTEGER NULL,          -- Aggregated N (number of samples) from output_*.csv
    test_description TEXT NULL,      -- Description associated with the test case from output_*.csv

    -- Individual run details (primarily from raw_results_{"{client}"}.csv)
    raw_gas_value TEXT NULL,         -- 'Gas' value from raw_*.csv, specific to this individual run
    raw_run_mgas_s REAL NULL,        -- The MGas/s for this specific individual run
    raw_run_description TEXT NULL,   -- Description from the raw_*.csv row, potentially more specific

    -- Test execution timestamps
    start_time TIMESTAMP WITH TIME ZONE NULL,  -- Test start timestamp

    -- Computer Specifications (parsed from system info, repeated per row, all nullable)
    spec_processor_type TEXT NULL,
    spec_system_os TEXT NULL,
    spec_kernel_release TEXT NULL,
    spec_kernel_version TEXT NULL,
    spec_machine_arch TEXT NULL,
    spec_processor_arch TEXT NULL,
    spec_ram_gb REAL NULL,
    spec_cpu_model TEXT NULL,
    spec_num_cpus INTEGER NULL,
    spec_cpu_ghz REAL NULL
);
    """
    return benchmark_data_table_sql

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
            # 1. Check if table exists
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s);", (table_name,))
            table_exists = cur.fetchone()
            if table_exists is None or not table_exists[0]: # Ensure fetchone result is checked properly
                logging.info(f"Table '{table_name}' does not exist. Creating it now.")
                create_table_sql = get_sql_for_benchmark_table(table_name)
                logging.debug(f"Executing CREATE TABLE statement:\n{create_table_sql}")
                cur.execute(create_table_sql)
                logging.info(f"Table '{table_name}' created successfully.")
            else:
                logging.info(f"Table '{table_name}' already exists. Checking for missing columns...")

                # Migration: columns to check and add if they don't exist
                # Format: (column_name, column_definition_for_add_column)
                columns_to_ensure: List[Tuple[str, str]] = [
                    ("client_version", "TEXT NULL"),
                    ("start_time", "TIMESTAMP WITH TIME ZONE NULL"),
                    # Add other columns here in the future for schema evolution
                    # e.g., ("new_feature_flag", "BOOLEAN DEFAULT FALSE")
                ]

                for col_name, col_definition in columns_to_ensure:
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
                        );
                    """, (table_name, col_name))
                    column_exists_result = cur.fetchone()
                    column_exists = column_exists_result[0] if column_exists_result else False

                    if not column_exists:
                        logging.info(f"Column '{col_name}' does not exist in table '{table_name}'. Adding it.")
                        # Note: F-strings for SQL construction are generally discouraged if inputs are not controlled.
                        # Here, col_name and col_definition are from the hardcoded 'columns_to_ensure' list, so it's acceptable.
                        alter_sql = f"ALTER TABLE public.{table_name} ADD COLUMN {col_name} {col_definition};"
                        logging.debug(f"Executing ALTER TABLE statement: {alter_sql}")
                        cur.execute(alter_sql)
                        logging.info(f"Column '{col_name}' added to table '{table_name}'.")
                    else:
                        logging.debug(f"Column '{col_name}' already exists in table '{table_name}'.")
            conn.commit()
            logging.info(f"Database schema for table '{table_name}' is up to date.")
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
