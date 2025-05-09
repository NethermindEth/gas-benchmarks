import psycopg2
import argparse
import sys
import getpass

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

def execute_sql_on_db(db_params: dict, sql_query: str, table_name: str):
    """
    Connects to the PostgreSQL database and executes the given SQL query.
    """
    conn = None
    try:
        print(f"Connecting to PostgreSQL database '{db_params['dbname']}' on {db_params['host']}:{db_params['port']}...")
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        print(f"Executing SQL to create table '{table_name}' (if it doesn't exist)...")
        cur.execute(sql_query)
        conn.commit()
        print(f"Table '{table_name}' ensured successfully (created if not existed).")
        cur.close()
    except (Exception, psycopg2.Error) as error:
        print(f"Error while connecting to PostgreSQL or executing query: {error}")
        if conn:
            conn.rollback() # Rollback any pending transaction
        sys.exit(1)
    finally:
        if conn:
            conn.close()
            print("PostgreSQL connection closed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate and execute PostgreSQL CREATE TABLE statement for benchmark data.")
    parser.add_argument("--db-host", required=True, help="PostgreSQL database host.")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL database port (default: 5432).")
    parser.add_argument("--db-user", required=True, help="PostgreSQL database user.")
    parser.add_argument("--db-name", required=True, help="PostgreSQL database name.")
    parser.add_argument("--table-name", default="benchmark_data", help="Name for the table to be created (default: benchmark_data).")
    
    args = parser.parse_args()

    try:
        db_password = getpass.getpass(prompt=f"Enter password for PostgreSQL user '{args.db_user}': ")
    except Exception as error:
        print(f"ERROR: Could not get password: {error}")
        sys.exit(1)

    db_params = {
        "host": args.db_host,
        "port": args.db_port,
        "user": args.db_user,
        "password": db_password,
        "dbname": args.db_name,
    }

    sql_to_execute = get_sql_for_benchmark_table(args.table_name)
    
    # print("\n-- SQL to be executed: --")
    # print(sql_to_execute)
    # print("--------------------------\n")
    
    execute_sql_on_db(db_params, sql_to_execute, args.table_name) 