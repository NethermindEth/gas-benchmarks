import psycopg2
import csv
import os
import argparse
import sys
import glob
import re
from bs4 import BeautifulSoup, Tag # For parsing HTML if computer_specs.txt is not found
import logging
from typing import Any, Dict, List, Optional, Tuple # Added for type hinting
from io import StringIO # Add this for bulk copy operations

# --- Constants ---
SPEC_MAPPING: Dict[str, str] = {
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

ALL_SPEC_KEYS: List[str] = [
    "spec_processor_type", "spec_system_os", "spec_kernel_release", "spec_kernel_version",
    "spec_machine_arch", "spec_processor_arch", "spec_ram_gb", "spec_cpu_model",
    "spec_num_cpus", "spec_cpu_ghz"
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

def bulk_insert_records(cursor: psycopg2.extensions.cursor, table_name: str, records: List[Dict[str, Any]]) -> None:
    """
    Bulk inserts records using PostgreSQL COPY command for much faster performance.
    
    Args:
        cursor: The database cursor object.
        table_name: The name of the table to insert data into.
        records: List of dictionaries where keys are column names and values are the data to insert.
    """
    if not records:
        return
        
    # Get column names from the first record
    columns = list(records[0].keys())
    
    # Create StringIO buffer for CSV data
    csv_buffer = StringIO()
    csv_writer = csv.writer(csv_buffer, delimiter='\t', quotechar='"', quoting=csv.QUOTE_MINIMAL)
    
    # Write records to CSV buffer
    for record in records:
        row = []
        for col in columns:
            value = record.get(col)
            if value is None:
                row.append('')  # Empty string for NULL values
            else:
                row.append(str(value))
        csv_writer.writerow(row)
    
    # Reset buffer position to beginning
    csv_buffer.seek(0)
    
    try:
        # Use COPY FROM with the CSV buffer
        cursor.copy_from(
            csv_buffer,
            table_name,
            columns=columns,
            sep='\t',
            null=''
        )
        logging.info(f"Bulk inserted {len(records)} records into {table_name}")
    except (psycopg2.DataError, psycopg2.IntegrityError) as error:
        logging.error(f"Data error during bulk insert: {error}")
        raise
    except psycopg2.Error as error:
        logging.error(f"Database error during bulk insert: {error}")
        raise
    except Exception as error:
        logging.error(f"Unexpected error during bulk insert: {error}")
        raise
    finally:
        csv_buffer.close()

def insert_benchmark_record(cursor: psycopg2.extensions.cursor, table_name: str, record_data: Dict[str, Any]) -> None:
    """
    Inserts a single record into the specified table.
    
    DEPRECATED: Use bulk_insert_records for better performance.

    Args:
        cursor: The database cursor object.
        table_name: The name of the table to insert data into.
        record_data: A dictionary where keys are column names and values are the data to insert.
    """
    columns = record_data.keys()
    values_placeholders = ", ".join(["%s"] * len(columns))
    sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_placeholders})"
    try:
        cursor.execute(sql, list(record_data.values()))
    except (psycopg2.DataError, psycopg2.IntegrityError) as error:
        logging.error(f"Data error inserting record: {error}\nSQL: {sql}\nRecord data: {record_data}")
        raise
    except psycopg2.Error as error:
        logging.error(f"Database error inserting record: {error}\nSQL: {sql}\nRecord data: {record_data}")
        raise
    except Exception as error:
        logging.error(f"Unexpected error inserting record: {error}\nSQL: {sql}\nRecord data: {record_data}")
        raise


# --- Computer Specs Parsing ---
def parse_ram(ram_string: Optional[str]) -> Optional[float]:
    """Parses RAM string (e.g., '16 GB') into a float (GB)."""
    if not ram_string: return None
    match = re.search(r"([\\d\\.]+)\\s*GB", ram_string, re.IGNORECASE)
    return float(match.group(1)) if match else None

def parse_cpu_ghz(ghz_string: Optional[str]) -> Optional[float]:
    """Parses CPU speed string (e.g., '2.5 GHz') into a float (GHz)."""
    if not ghz_string: return None
    match = re.search(r"([\\d\\.]+)\\s*GHz", ghz_string, re.IGNORECASE)
    return float(match.group(1)) if match else None

def parse_opcount(raw_value: Optional[str]) -> Optional[int]:
    """
    Parse an operation count that may include K/M suffixes.
    Examples:
      "50000K" -> 50000000
      "2500000" -> 2500000
    """
    if raw_value is None:
        return None

    value = raw_value.strip()
    if value == "":
        return None

    match = re.match(r"^(?P<num>[0-9]+(?:\.[0-9]+)?)(?P<suffix>[kKmM]?)$", value)
    if not match:
        raise ValueError(f"Unrecognized opcount format: {raw_value}")

    number_part = float(match.group("num"))
    return int(number_part * 1_000)

def parse_specs_from_text(spec_text_content: str) -> Dict[str, Any]:
    """
    Parses computer specifications from a block of text.

    Args:
        spec_text_content: A string containing the computer specifications.

    Returns:
        A dictionary with parsed specification keys and their values.
    """
    logging.debug("--- spec_text_content received by parse_specs_from_text: ---\n%s\n--- End of spec_text_content ---", spec_text_content)
    specs: Dict[str, Any] = {}

    if not spec_text_content or not spec_text_content.strip():
        logging.debug("spec_text_content is empty or whitespace only.")
        return specs

    for line in spec_text_content.splitlines():
        line = line.strip()
        if not line:
            logging.debug("Skipping empty line.")
            continue
        logging.debug(f"Processing line: '{line}'")
        matched_key_for_line = False
        for key_prefix, db_field in SPEC_MAPPING.items():
            if line.startswith(key_prefix):
                logging.debug(f"Matched prefix '{key_prefix}' for db_field '{db_field}'.")
                matched_key_for_line = True
                parts = line.split(":", 1)
                if len(parts) == 2:
                    value = parts[1].strip()
                    logging.debug(f"Extracted value: '{value}'")
                    if db_field == "spec_ram_gb":
                        parsed_val = parse_ram(value)
                        specs[db_field] = parsed_val
                        logging.debug(f"Parsed RAM: {parsed_val}")
                    elif db_field == "spec_cpu_ghz":
                        parsed_val = parse_cpu_ghz(value)
                        specs[db_field] = parsed_val
                        logging.debug(f"Parsed CPU GHz: {parsed_val}")
                    elif db_field == "spec_num_cpus":
                        try:
                            specs[db_field] = int(value)
                            logging.debug(f"Parsed Num CPUs: {specs[db_field]}")
                        except ValueError:
                            specs[db_field] = None
                            logging.debug(f"ValueError converting '{value}' to int for {db_field}.")
                    else:
                        specs[db_field] = value
                        logging.debug(f"Assigned '{value}' to {db_field}.")
                else:
                    logging.debug(f"Line '{line}' started with prefix '{key_prefix}' but split on ':' did not yield 2 parts. Parts: {parts}")
                break
        if not matched_key_for_line:
            logging.debug(f"No matching prefix found for line: '{line}'.")

    logging.debug("--- specs dictionary before returning from parse_specs_from_text: ---\n%s\n--- End of parse_specs_from_text debug ---", specs)
    return specs

def get_computer_specs(reports_dir: str) -> Dict[str, Any]:
    """
    Retrieves computer specifications either from 'computer_specs.txt' or 'index.html'
    found in the specified reports directory.

    Args:
        reports_dir: The directory where specification files are located.

    Returns:
        A dictionary containing computer specifications. Defaults to None for missing values.
    """
    specs_data: Dict[str, Any] = {}
    specs_file_path = os.path.join(reports_dir, "computer_specs.txt")
    html_index_path = os.path.join(reports_dir, "index.html")

    try:
        if os.path.exists(specs_file_path):
            logging.info(f"Reading computer specs from: {specs_file_path}")
            with open(specs_file_path, 'r', encoding='utf-8') as f: # Added encoding
                content = f.read()
            specs_data = parse_specs_from_text(content)
        elif os.path.exists(html_index_path):
            logging.info(f"Reading computer specs from HTML: {html_index_path}")
            with open(html_index_path, 'r', encoding='utf-8') as f: # Added encoding
                soup = BeautifulSoup(f, 'lxml')
                pre_tag = soup.find('pre')
                if pre_tag:
                    specs_data = parse_specs_from_text(pre_tag.get_text())
                else:
                    logging.warning("Could not find <pre> tag with specs in index.html")
        else:
            logging.warning("Computer specs source (computer_specs.txt or index.html) not found in reports directory: %s", reports_dir)
    except FileNotFoundError as e:
        logging.error(f"Specs file not found during parsing: {e}")
    except Exception as e:
        logging.error(f"Error parsing computer specs: {e}", exc_info=True) # Added exc_info for stack trace

    # Ensure all spec keys are present, defaulting to None if not parsed
    for key in ALL_SPEC_KEYS:
        specs_data.setdefault(key, None) # Use setdefault for cleaner way to add if missing

    return specs_data

# --- CSV Processing & Data Population ---
def load_aggregated_stats(output_csv_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Loads aggregated statistics from an output_{client}.csv file.

    Args:
        output_csv_path: Path to the CSV file containing aggregated statistics.

    Returns:
        A dictionary mapping test titles to their aggregated statistics.
    """
    aggregated_map: Dict[str, Dict[str, Any]] = {}
    try:
        if not os.path.exists(output_csv_path):
            logging.warning(f"Aggregated stats file not found: {output_csv_path}")
            return aggregated_map

        with open(output_csv_path, 'r', newline='', encoding='utf-8') as csvfile: # Added encoding
            reader = csv.DictReader(csvfile)
            for row in reader:
                title = row.get('Title')
                if title:
                    try:
                        aggregated_map[title] = {
                            'max_mgas_s': float(row['Max (MGas/s)']) if row.get('Max (MGas/s)') and row['Max (MGas/s)'].strip() else None,
                            'p50_mgas_s': float(row['p50 (MGas/s)']) if row.get('p50 (MGas/s)') and row['p50 (MGas/s)'].strip() else None,
                            'p95_mgas_s': float(row['p95 (MGas/s)']) if row.get('p95 (MGas/s)') and row['p95 (MGas/s)'].strip() else None,
                            'p99_mgas_s': float(row['p99 (MGas/s)']) if row.get('p99 (MGas/s)') and row['p99 (MGas/s)'].strip() else None,
                            'min_mgas_s': float(row['Min (MGas/s)']) if row.get('Min (MGas/s)') and row['Min (MGas/s)'].strip() else None,
                            'n_samples': int(row['N']) if row.get('N') and row['N'].strip() else None,
                            # Add timestamp and duration fields if they exist
                            'start_time': row.get('Start Time') if row.get('Start Time') else None,
                            'end_time': row.get('End Time') if row.get('End Time') else None,
                            'test_duration': float(row['Duration (ms)']) if row.get('Duration (ms)') and row['Duration (ms)'].strip() else None,
                            'fcu_duration': float(row['FCU time (ms)']) if row.get('FCU time (ms)') and row['FCU time (ms)'].strip() else None,
                            'np_duration': float(row['NP time (ms)']) if row.get('NP time (ms)') and row['NP time (ms)'].strip() else None,
                            'test_description': row.get('Description')
                        }
                    except ValueError as ve:
                        logging.warning(f"Skipping row in {output_csv_path} due to data conversion error for title '{title}': {ve} - Row: {row}")
                    except KeyError as ke:
                        logging.warning(f"Skipping row in {output_csv_path} due to missing key for title '{title}': {ke} - Row: {row}")
    except FileNotFoundError:
        logging.warning(f"Aggregated stats file not found: {output_csv_path}") # Already handled above, but good for robustness
    except Exception as e:
        logging.error(f"Error reading aggregated stats from {output_csv_path}: {e}", exc_info=True)
    return aggregated_map

# --- Client Version Parsing ---
def extract_client_version_from_text_content(html_text_content: Optional[str], client_name: str) -> Optional[str]:
    """
    Extracts the client version from a block of HTML text content for a specific client.
    It expects lines in the format: "ClientName - image/path:version - Benchmarking Report"
    Args:
        html_text_content: The text content (e.g., from soup.get_text()).
        client_name: The name of the client (e.g., 'nethermind', 'geth') to find the version for.
    Returns:
        The extracted version string if found, otherwise None.
    """
    if not html_text_content:
        return None

    lines = html_text_content.splitlines()
    logging.debug(f"Attempting to extract version for '{client_name}'. Total lines from HTML: {len(lines)}")

    # Regex to capture the version from a line confirmed to be for the target client.
    # Example line: "Nethermind - nethermindeth/nethermind:release-1.31.9 - Benchmarking Report"
    # We want to capture "release-1.31.9"
    # Breaking down the version regex part: (?P<version>[^\s\-]+(?:-[^\s\-]+)*)
    #   [^\s\-]+             => Matches one or more characters that are NOT whitespace and NOT a hyphen.
    #                          (e.g., "release", "v1.2.3", "0.1.0")
    #   (?:-[^\s\-]+)*      => Optionally matches a group: (hyphen, then more non-whitespace/non-hyphen chars).
    #                          This group can repeat. (e.g., "-1.31.9", "-beta.1")
    version_extraction_pattern = re.compile(
        rf"""
        ^                                     # Start of the (cleaned) line
        {re.escape(client_name)}              # The client name (case-insensitive due to how line is found + re.IGNORECASE)
        \s*-\s*                               # Separator: hyphen surrounded by optional whitespace
        [^:]+:                                # Image/path part (e.g., "nethermindeth/nethermind:") - anything not a colon, then colon
        (?P<version>[^\s\-]+(?:-[^\s\-]+)*)   # The version string (captured named group 'version')
        \s*-\s*Benchmarking\sReport          # Trailing part: " - Benchmarking Report"
        \s*                                   # Optional trailing whitespace from original HTML structure
        $                                     # End of the (cleaned) line
        """,
        re.IGNORECASE | re.VERBOSE
    )

    for i, line_content in enumerate(lines):
        cleaned_line = line_content.strip() # Important: strip whitespace from the line

        # Check if the cleaned line starts with the client name (case-insensitive)
        # This is a preliminary filter before applying the more complex regex.
        if cleaned_line.lower().startswith(client_name.lower()):
            logging.debug(f"Potential match for '{client_name}' on line {i} (cleaned): '{cleaned_line}'")

            match = version_extraction_pattern.match(cleaned_line) # Use .match() because of ^ anchor
            if match:
                version = match.group("version")
                logging.info(f"Successfully extracted version '{version}' for client '{client_name}' from line: '{cleaned_line}'")
                return version
            else:
                # If the full strict pattern fails, try a more lenient one just for this line that we know starts with the client name.
                # This lenient regex looks for "ClientName ... :VERSION_PART ..."
                lenient_pattern = re.compile(
                    rf"""
                    {re.escape(client_name)}   # Client name (case-insensitive due to re.IGNORECASE)
                    .*?                        # Anything in between (non-greedy)
                    :                          # Colon before version
                    (?P<version>[^\s\-]+(?:-[^\s\-]+)*) # Version part - same robust version capture
                    """,
                    re.IGNORECASE | re.VERBOSE
                )
                lenient_match_obj = lenient_pattern.search(cleaned_line) # Use search here, it's less anchored
                if lenient_match_obj:
                    version = lenient_match_obj.group("version")
                    logging.info(f"Extracted version '{version}' for client '{client_name}' using *lenient* fallback from line: '{cleaned_line}'")
                    return version
                else:
                    logging.warning(f"Line started with '{client_name}' but no version pattern (strict or lenient) matched: '{cleaned_line}'")

    logging.warning(f"No line found containing client '{client_name}' and a parsable version string.")
    return None

def populate_data_for_client(
    cursor: psycopg2.extensions.cursor,
    table_name: str,
    client_name: str,
    client_version: Optional[str],
    reports_dir: str,
    aggregated_stats_map: Dict[str, Dict[str, Any]],
    computer_specs: Dict[str, Any]
) -> int:
    """
    Populates the database with raw benchmark data for a specific client using bulk insert.

    Args:
        cursor: Database cursor.
        table_name: Name of the target database table.
        client_name: Name of the client being processed.
        client_version: Version of the client being processed.
        reports_dir: Directory containing the raw results CSV.
        aggregated_stats_map: Pre-loaded aggregated statistics for this client.
        computer_specs: Parsed computer specifications.

    Returns:
        The number of records inserted for this client.
    """
    raw_csv_path = os.path.join(reports_dir, f"raw_results_{client_name}.csv")

    try:
        if not os.path.exists(raw_csv_path):
            logging.warning(f"Raw results file not found for client {client_name}: {raw_csv_path}")
            return 0

        records_to_insert: List[Dict[str, Any]] = []
        logging.info(f"Processing raw results for client: {client_name} from {raw_csv_path}")

        with open(raw_csv_path, 'r', newline='', encoding='utf-8') as csvfile: # Added encoding
            reader = csv.reader(csvfile)
            header = next(reader, None)
            if not header:
                logging.warning(f"Raw results file is empty or has no header: {raw_csv_path}")
                return 0

            if len(header) < 3: # Expecting at least 'Test Case', 'Gas', (optional Opcount) and one run/description column
                logging.warning(f"Raw results file {raw_csv_path} has unexpected header format (less than 3 columns): {header}")
                return 0

            try:
                opcount_index = next(i for i, col in enumerate(header) if col.strip().lower() == "opcount")
            except StopIteration:
                opcount_index = None

            description_index = len(header) - 1
            run_start_index = 2 if opcount_index is None else opcount_index + 1
            if run_start_index >= description_index:
                logging.warning(f"Raw results file {raw_csv_path} has no run columns between gas/opcount and description. Header: {header}")
                return 0

            run_column_headers = header[run_start_index:description_index]
            values_are_durations = bool(run_column_headers) and all(
                any(token in col.lower() for token in ("duration", "time", "ms"))
                for col in run_column_headers
            ) and not any("mgas" in col.lower() for col in run_column_headers)

            for i, row in enumerate(reader):
                if len(row) != len(header):
                    logging.warning(f"Skipping malformed row {i+2} in {raw_csv_path}. Expected {len(header)} columns, got {len(row)}. Row: {row}")
                    continue

                test_case_name_raw = row[0]
                raw_gas_value = row[1] if len(row) > 1 else None
                raw_opcount_value = row[opcount_index] if opcount_index is not None and opcount_index < len(row) else None
                # raw_run_description is the last column if header > 2, otherwise it might be missing
                raw_run_description = row[description_index] if len(row) > description_index else None # Adjusted access
                run_values_str = row[run_start_index:description_index] if len(row) >= description_index else [] # Adjusted access

                agg_stats = aggregated_stats_map.get(test_case_name_raw, {})

                if not run_values_str and len(header) == 2: # Handle case with only 'Test Case', 'Gas'
                     logging.debug(f"Row for '{test_case_name_raw}' seems to only have Test Case and Gas value, no individual runs. Skipping run processing.")
                     # Decide if you want to insert a record with just this minimal info
                     # For now, we expect run values to insert.

                gas_value_float: Optional[float] = None
                if raw_gas_value not in ("", None):
                    try:
                        gas_value_float = float(raw_gas_value)
                    except ValueError:
                        logging.warning(f"Could not convert gas value '{raw_gas_value}' to float for {test_case_name_raw}. Will keep raw string and skip per-run MGas/s calculation.")

                opcount_value: Optional[int] = None
                if raw_opcount_value not in ("", None):
                    try:
                        opcount_value = parse_opcount(raw_opcount_value)
                    except ValueError as ve:
                        logging.warning(f"Could not parse opcount '{raw_opcount_value}' for {test_case_name_raw}: {ve}")

                for run_value_str in run_values_str:
                    try:
                        run_value = float(run_value_str) if run_value_str.strip() else None
                    except ValueError:
                        logging.warning(f"Could not convert run value '{run_value_str}' to float for {test_case_name_raw}. Skipping this run.")
                        continue
                    if run_value is None:
                        logging.debug(f"Skipping empty run value for {test_case_name_raw}.")
                        continue

                    raw_run_duration_ms: Optional[float] = None
                    raw_run_mgas_s: Optional[float] = None

                    if values_are_durations:
                        raw_run_duration_ms = run_value
                        if gas_value_float is not None and raw_run_duration_ms > 0:
                            raw_run_mgas_s = (gas_value_float / raw_run_duration_ms) * 1000.0
                    else:
                        raw_run_mgas_s = run_value
                        if gas_value_float is not None and raw_run_mgas_s > 0:
                            raw_run_duration_ms = (gas_value_float / raw_run_mgas_s) * 1000.0

                    start_time = agg_stats.get('start_time')
                    if start_time in (0, "0", "", None):
                        start_time = None
                    
                    end_time = agg_stats.get('end_time')
                    if end_time in (0, "0", "", None):
                        end_time = None
                    
                    test_duration = agg_stats.get('test_duration')
                    fcu_duration = agg_stats.get('fcu_duration')
                    np_duration = agg_stats.get('np_duration')
                    
                    record: Dict[str, Any] = {
                        'client_name': client_name,
                        'client_version': client_version,
                        'test_title': test_case_name_raw,
                        'max_mgas_s': agg_stats.get('max_mgas_s'),
                        'p50_mgas_s': agg_stats.get('p50_mgas_s'),
                        'p95_mgas_s': agg_stats.get('p95_mgas_s'),
                        'p99_mgas_s': agg_stats.get('p99_mgas_s'),
                        'min_mgas_s': agg_stats.get('min_mgas_s'),
                        'n_samples': agg_stats.get('n_samples'),
                        'test_description': agg_stats.get('test_description'),
                        'raw_gas_value': raw_gas_value,
                        'opcount': opcount_value,
                        'raw_run_duration_ms': raw_run_duration_ms,
                        'raw_run_mgas_s': raw_run_mgas_s,
                        'raw_run_description': raw_run_description, # This is from the raw data row
                        'start_time': start_time,
                        'end_time': end_time,
                        'test_duration': test_duration,
                        'fcu_duration': fcu_duration,
                        'np_duration': np_duration,
                        **computer_specs
                    }
                    records_to_insert.append(record)

        # Bulk insert all records for this client
        if records_to_insert:
            bulk_insert_records(cursor, table_name, records_to_insert)
            inserted_count = len(records_to_insert)
        else:
            inserted_count = 0

    except FileNotFoundError:
        logging.warning(f"Raw results file not found for client {client_name}: {raw_csv_path}") # Already handled above, defensive.
        return 0
    except Exception as e:
        logging.error(f"Error processing raw results file {raw_csv_path}: {e}", exc_info=True)
        return 0
    
    return inserted_count

# --- Main Execution ---
def main() -> None:
    """
    Main function to parse arguments, connect to DB, and populate benchmark data.
    """
    # Setup logging
    # For more advanced configuration, consider a config file or more CLI args for log level, file output etc.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)] # Ensures logs go to stdout
    )

    parser = argparse.ArgumentParser(
        description="Parse benchmark CSVs and populate PostgreSQL database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Shows default values in help
    )
    parser.add_argument("--reports-dir", required=True, help="Directory containing the report files (output_*.csv, raw_results_*.csv, and optionally computer_specs.txt or index.html).")
    parser.add_argument("--db-host", required=True, help="PostgreSQL database host.")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL database port.")
    parser.add_argument("--db-user", required=True, help="PostgreSQL database user.")
    # Note: Use github actions secrets for the password.
    parser.add_argument("--db-password", required=True, help="PostgreSQL database password.")
    parser.add_argument("--db-name", required=True, help="PostgreSQL database name.")
    parser.add_argument("--table-name", default="benchmark_data", help="Name of the target table in the database.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )

    args = parser.parse_args()

    # Update logging level based on CLI argument
    logging.getLogger().setLevel(args.log_level.upper())


    db_params: Dict[str, Any] = {
        "host": args.db_host,
        "port": args.db_port,
        "user": args.db_user,
        "password": args.db_password,
        "dbname": args.db_name,
    }

    conn = get_db_connection(db_params)
    if not conn:
        logging.critical("Failed to establish database connection. Exiting.")
        sys.exit(1)

    total_records_inserted = 0
    main_page_text_content: Optional[str] = None

    # Try to read index.html and get its full text content
    html_index_path = os.path.join(args.reports_dir, "index.html")
    if os.path.exists(html_index_path):
        try:
            with open(html_index_path, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f, 'lxml')
                main_page_text_content = soup.get_text(separator='\n')
                if main_page_text_content:
                    logging.info(f"Successfully read and extracted text content from {html_index_path}")
                    # Prepare the string for logging, ensuring no backslashes in f-string expressions
                    log_sample_text = main_page_text_content[:500].replace('\n', ' ')
                    logging.debug(f"First 500 chars of extracted text from {html_index_path}: {log_sample_text}")
                else:
                    logging.warning(f"Could not extract any text content from {html_index_path} using soup.get_text()")
        except Exception as e:
            logging.error(f"Error reading or parsing {html_index_path}: {e}", exc_info=True)
    else:
        logging.warning(f"Main index.html not found at {html_index_path}, client versions cannot be extracted.")

    try: # Main try block for operations that need DB connection
        with conn.cursor() as cursor: # Use context manager for cursor
            logging.info(f"Parsing computer specifications from: {args.reports_dir}")
            computer_specs = get_computer_specs(args.reports_dir)
            logging.debug(f"Computer specs parsed: {computer_specs}")

            output_csv_pattern = os.path.join(args.reports_dir, "output_*.csv")
            client_files = glob.glob(output_csv_pattern)

            if not client_files:
                logging.warning(f"No 'output_*.csv' files found in {args.reports_dir}. Cannot determine clients.")
                # No sys.exit here, connection will be closed in finally
                return # Exit main if no clients

            clients: List[str] = []
            for f_path in client_files:
                filename = os.path.basename(f_path)
                match = re.match(r"output_(.+)\.csv", filename)
                if match:
                    clients.append(match.group(1))
                else:
                    logging.warning(f"Could not parse client name from {filename}")

            if not clients:
                logging.warning(f"No clients could be determined from 'output_*.csv' files in {args.reports_dir}. Exiting.")
                return

            logging.info(f"Found clients: {clients}")

            for client_name in clients:
                logging.info(f"--- Processing client: {client_name} ---")

                # Extract client version for the current client from the HTML text content
                client_version: Optional[str] = None
                if main_page_text_content:
                    client_version = extract_client_version_from_text_content(main_page_text_content, client_name)

                if not client_version:
                    logging.warning(f"Could not determine version for client '{client_name}' from the content of {html_index_path}.")

                output_csv_path = os.path.join(args.reports_dir, f"output_{client_name}.csv")
                aggregated_stats_map = load_aggregated_stats(output_csv_path)

                if not aggregated_stats_map:
                    logging.warning(f"No aggregated stats loaded for client {client_name}, skipping raw data processing for this client.")
                    continue

                inserted_for_client = populate_data_for_client(
                    cursor, args.table_name, client_name, client_version, # Added client_version
                    args.reports_dir, aggregated_stats_map, computer_specs
                )
                total_records_inserted += inserted_for_client
                logging.info(f"Inserted {inserted_for_client} records for client {client_name}.")

        # Commit once after all clients are processed
        conn.commit()
        logging.info(f"\nSuccessfully committed all changes. Total records inserted: {total_records_inserted}.")

    except (psycopg2.Error, Exception) as e: # Catch DB errors or other exceptions during processing
        logging.error(f"An error occurred during database operations or processing: {e}", exc_info=True)
        if conn: # conn might be None if initial connection failed and was handled by get_db_connection returning None
            try:
                conn.rollback()
                logging.info("Database transaction rolled back.")
            except psycopg2.Error as rb_error:
                logging.error(f"Error during rollback: {rb_error}")
    finally:
        if conn:
            conn.close()
            logging.info("PostgreSQL connection closed.")

if __name__ == "__main__":
    main()
