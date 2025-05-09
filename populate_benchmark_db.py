import psycopg2
import csv
import os
import argparse
# import getpass # Removed for CLI password
import sys
import glob
import re
from bs4 import BeautifulSoup # For parsing HTML if computer_specs.txt is not found

# --- Database Interaction ---
def get_db_connection(db_params):
    conn = None
    try:
        conn = psycopg2.connect(**db_params)
        print(f"Successfully connected to database '{db_params['dbname']}' on {db_params['host']}.")
    except (Exception, psycopg2.Error) as error:
        print(f"Error connecting to PostgreSQL: {error}")
        sys.exit(1)
    return conn

def insert_benchmark_record(cursor, table_name, record_data):
    """Inserts a single record into the specified table."""
    columns = record_data.keys()
    values_placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_placeholders})"
    try:
        cursor.execute(sql, list(record_data.values()))
    except (Exception, psycopg2.Error) as error:
        print(f"Error inserting record: {error}\nRecord data: {record_data}")
        # Optionally re-raise or handle more gracefully

# --- Computer Specs Parsing ---
def parse_ram(ram_string):
    if not ram_string: return None
    match = re.search(r"([\d\.]+)\s*GB", ram_string, re.IGNORECASE)
    return float(match.group(1)) if match else None

def parse_cpu_ghz(ghz_string):
    if not ghz_string: return None
    match = re.search(r"([\d\.]+)\s*GHz", ghz_string, re.IGNORECASE)
    return float(match.group(1)) if match else None

def parse_specs_from_text(spec_text_content):
    print("DEBUG: --- spec_text_content received by parse_specs_from_text: ---")
    print(f'''
{spec_text_content}
''')
    print("DEBUG: --- End of spec_text_content ---")
    specs = {}
    mapping = {
        "Processor:": "spec_processor_type",
        "System:": "spec_system_os",
        "Release:": "spec_kernel_release",
        "Version:": "spec_kernel_version",
        "Machine:": "spec_machine_arch",
        "Processor Architecture:": "spec_processor_arch",
        "RAM:": "spec_ram_gb",
        "CPU:": "spec_cpu_model",
        "Numbers of CPU:": "spec_num_cpus",
        "CPU GHz:": "spec_cpu_ghz"
    }
    if not spec_text_content or not spec_text_content.strip():
        print("DEBUG: spec_text_content is empty or whitespace only.")
        return specs

    for line in spec_text_content.splitlines():
        line = line.strip()
        if not line:
            print("DEBUG: Skipping empty line.")
            continue
        print(f"DEBUG: Processing line: '{line}'")
        matched_key_for_line = False
        for key_prefix, db_field in mapping.items():
            if line.startswith(key_prefix):
                print(f"DEBUG: Matched prefix '{key_prefix}' for db_field '{db_field}'.")
                matched_key_for_line = True
                parts = line.split(":", 1)
                if len(parts) == 2:
                    value = parts[1].strip()
                    print(f"DEBUG: Extracted value: '{value}'")
                    if db_field == "spec_ram_gb":
                        parsed_val = parse_ram(value)
                        specs[db_field] = parsed_val
                        print(f"DEBUG: Parsed RAM: {parsed_val}")
                    elif db_field == "spec_cpu_ghz":
                        parsed_val = parse_cpu_ghz(value)
                        specs[db_field] = parsed_val
                        print(f"DEBUG: Parsed CPU GHz: {parsed_val}")
                    elif db_field == "spec_num_cpus":
                        try:
                            specs[db_field] = int(value)
                            print(f"DEBUG: Parsed Num CPUs: {specs[db_field]}")
                        except ValueError:
                            specs[db_field] = None
                            print(f"DEBUG: ValueError converting '{value}' to int for {db_field}.")
                    else:
                        specs[db_field] = value
                        print(f"DEBUG: Assigned '{value}' to {db_field}.")
                else:
                    print(f"DEBUG: Line '{line}' started with prefix '{key_prefix}' but split on ':' did not yield 2 parts. Parts: {parts}")
                break 
        if not matched_key_for_line:
            print(f"DEBUG: No matching prefix found for line: '{line}'.")
            
    print("DEBUG: --- specs dictionary before returning from parse_specs_from_text: ---")
    print(specs)
    print("DEBUG: --- End of parse_specs_from_text debug ---")
    return specs

def get_computer_specs(reports_dir):
    specs_data = {}
    specs_file_path = os.path.join(reports_dir, "computer_specs.txt")
    html_index_path = os.path.join(reports_dir, "index.html")

    try:
        if os.path.exists(specs_file_path):
            print(f"Reading computer specs from: {specs_file_path}")
            with open(specs_file_path, 'r') as f:
                content = f.read()
            specs_data = parse_specs_from_text(content)
        elif os.path.exists(html_index_path):
            print(f"Reading computer specs from HTML: {html_index_path}")
            with open(html_index_path, 'r') as f:
                soup = BeautifulSoup(f, 'lxml') # 'html.parser' is a fallback
                pre_tag = soup.find('pre') # report_html.py puts specs in a <pre> tag
                if pre_tag:
                    specs_data = parse_specs_from_text(pre_tag.get_text())
                else:
                    print("Could not find <pre> tag with specs in index.html")
        else:
            print("Computer specs source (computer_specs.txt or index.html) not found in reports directory.")
    except Exception as e:
        print(f"Error parsing computer specs: {e}")
    
    # Ensure all spec keys are present, defaulting to None if not parsed
    all_spec_keys = [
        "spec_processor_type", "spec_system_os", "spec_kernel_release", "spec_kernel_version",
        "spec_machine_arch", "spec_processor_arch", "spec_ram_gb", "spec_cpu_model",
        "spec_num_cpus", "spec_cpu_ghz"
    ]
    for key in all_spec_keys:
        if key not in specs_data:
            specs_data[key] = None
            
    return specs_data

# --- CSV Processing & Data Population ---
def load_aggregated_stats(output_csv_path):
    """Loads aggregated statistics from an output_{client}.csv file."""
    aggregated_map = {}
    if not os.path.exists(output_csv_path):
        print(f"Warning: Aggregated stats file not found: {output_csv_path}")
        return aggregated_map
    try:
        with open(output_csv_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                title = row.get('Title')
                if title:
                    try:
                        aggregated_map[title] = {
                            'max_mgas_s': float(row['Max (MGas/s)']) if row.get('Max (MGas/s)') else None,
                            'p50_mgas_s': float(row['p50 (MGas/s)']) if row.get('p50 (MGas/s)') else None,
                            'p95_mgas_s': float(row['p95 (MGas/s)']) if row.get('p95 (MGas/s)') else None,
                            'p99_mgas_s': float(row['p99 (MGas/s)']) if row.get('p99 (MGas/s)') else None,
                            'min_mgas_s': float(row['Min (MGas/s)']) if row.get('Min (MGas/s)') else None,
                            'n_samples': int(row['N']) if row.get('N') else None,
                            'test_description': row.get('Description')
                        }
                    except ValueError as ve:
                        print(f"Warning: Skipping row in {output_csv_path} due to data conversion error for title '{title}': {ve} - Row: {row}")
                    except KeyError as ke:
                        print(f"Warning: Skipping row in {output_csv_path} due to missing key for title '{title}': {ke} - Row: {row}")
    except Exception as e:
        print(f"Error reading aggregated stats from {output_csv_path}: {e}")
    return aggregated_map

def populate_data_for_client(
    cursor, table_name, client_name, reports_dir, aggregated_stats_map, computer_specs
):
    raw_csv_path = os.path.join(reports_dir, f"raw_results_{client_name}.csv")
    if not os.path.exists(raw_csv_path):
        print(f"Warning: Raw results file not found for client {client_name}: {raw_csv_path}")
        return 0 # No records inserted

    inserted_count = 0
    print(f"Processing raw results for client: {client_name} from {raw_csv_path}")
    try:
        with open(raw_csv_path, 'r', newline='') as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, None) # Skip header
            if not header:
                print(f"Warning: Raw results file is empty or has no header: {raw_csv_path}")
                return 0
            
            # Expecting header: 'Test Case', 'Gas', Run 1...Run N, 'Description'
            # Number of run columns is len(header) - 3 (Test Case, Gas, Description)
            if len(header) < 3:
                print(f"Warning: Raw results file {raw_csv_path} has unexpected header format: {header}")
                return 0

            for i, row in enumerate(reader):
                if len(row) != len(header):
                    print(f"Warning: Skipping malformed row {i+2} in {raw_csv_path}. Expected {len(header)} columns, got {len(row)}. Row: {row}")
                    continue
                
                test_case_name_raw = row[0]
                raw_gas_value = row[1]
                raw_run_description = row[-1]
                run_mgas_s_values_str = row[2:-1]

                agg_stats = aggregated_stats_map.get(test_case_name_raw, {})

                for run_value_str in run_mgas_s_values_str:
                    try:
                        raw_run_mgas_s = float(run_value_str)
                    except ValueError:
                        # print(f"Warning: Could not convert run value '{run_value_str}' to float for {test_case_name_raw}. Skipping this run.")
                        continue # Skip this specific run value if it's not a valid float

                    record = {
                        'client_name': client_name,
                        'test_title': test_case_name_raw,
                        'max_mgas_s': agg_stats.get('max_mgas_s'),
                        'p50_mgas_s': agg_stats.get('p50_mgas_s'),
                        'p95_mgas_s': agg_stats.get('p95_mgas_s'),
                        'p99_mgas_s': agg_stats.get('p99_mgas_s'),
                        'min_mgas_s': agg_stats.get('min_mgas_s'),
                        'n_samples': agg_stats.get('n_samples'),
                        'test_description': agg_stats.get('test_description'),
                        'raw_gas_value': raw_gas_value,
                        'raw_run_mgas_s': raw_run_mgas_s,
                        'raw_run_description': raw_run_description,
                        **computer_specs # Spread the computer spec dictionary
                    }
                    insert_benchmark_record(cursor, table_name, record)
                    inserted_count += 1
    except Exception as e:
        print(f"Error processing raw results file {raw_csv_path}: {e}")
    return inserted_count

# --- Main Execution ---
def main():
    parser = argparse.ArgumentParser(description="Parse benchmark CSVs and populate PostgreSQL database.")
    parser.add_argument("--reports-dir", required=True, help="Directory containing the report files (output_*.csv, raw_results_*.csv, and optionally computer_specs.txt or index.html).")
    parser.add_argument("--db-host", required=True, help="PostgreSQL database host.")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL database port.")
    parser.add_argument("--db-user", required=True, help="PostgreSQL database user.")
    parser.add_argument("--db-password", required=True, help="PostgreSQL database password.")
    parser.add_argument("--db-name", required=True, help="PostgreSQL database name.")
    parser.add_argument("--table-name", default="benchmark_data", help="Name of the target table in the database.")
    
    args = parser.parse_args()

    db_params = {
        "host": args.db_host,
        "port": args.db_port,
        "user": args.db_user,
        "password": args.db_password,
        "dbname": args.db_name,
    }

    conn = get_db_connection(db_params)
    if not conn:
        sys.exit(1)
    
    cursor = conn.cursor()
    total_records_inserted = 0

    print(f"Parsing computer specifications from: {args.reports_dir}")
    computer_specs = get_computer_specs(args.reports_dir)
    # print(f"Computer specs parsed: {computer_specs}")

    # Find clients based on output_*.csv files
    output_csv_pattern = os.path.join(args.reports_dir, "output_*.csv")
    client_files = glob.glob(output_csv_pattern)
    
    if not client_files:
        print(f"No 'output_*.csv' files found in {args.reports_dir}. Cannot determine clients.")
        cursor.close()
        conn.close()
        sys.exit(1)

    clients = []
    for f_path in client_files:
        filename = os.path.basename(f_path)
        # Filename format: output_CLIENTNAME.csv
        match = re.match(r"output_(.+)\.csv", filename)
        if match:
            clients.append(match.group(1))
        else:
            print(f"Warning: Could not parse client name from {filename}")

    print(f"Found clients: {clients}")

    for client_name in clients:
        print(f"--- Processing client: {client_name} ---")
        output_csv_path = os.path.join(args.reports_dir, f"output_{client_name}.csv")
        aggregated_stats_map = load_aggregated_stats(output_csv_path)
        
        if not aggregated_stats_map:
            print(f"No aggregated stats loaded for client {client_name}, skipping raw data processing for this client.")
            continue

        inserted_for_client = populate_data_for_client(
            cursor, args.table_name, client_name, args.reports_dir, 
            aggregated_stats_map, computer_specs
        )
        total_records_inserted += inserted_for_client
        print(f"Inserted {inserted_for_client} records for client {client_name}.")

    try:
        conn.commit()
        print(f"\nSuccessfully committed all changes. Total records inserted: {total_records_inserted}.")
    except (Exception, psycopg2.Error) as e:
        print(f"Error committing changes to database: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        print("PostgreSQL connection closed.")

if __name__ == "__main__":
    main() 