# This file will merge the results from different runs and test cases into a single file based only on the information
# on the folder structure.

import argparse
import os

import yaml
from bs4 import BeautifulSoup
import utils
import csv


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--first', type=str, help='Path to gather the results', default='reports/')
    parser.add_argument('--second', type=str, help='results', default='reports_2/')
    parser.add_argument('--output', type=str, help='Number of runs the program will process', default='merge/')

    # Parse command-line arguments
    args = parser.parse_args()
    first = args.first
    second = args.second
    output = args.output

    # Get the list of files from both folders, and merge them into a single set without duplicates
    first_files = set(os.listdir(first))
    second_files = set(os.listdir(second))
    files = first_files.union(second_files)

    # Create the output folder if it doesn't exist
    if not os.path.exists(output):
        os.makedirs(output)

    # Iterate over the files and merge them
    for file in files:
        first_file = os.path.join(first, file)
        second_file = os.path.join(second, file)
        output_file = os.path.join(output, file)

        # If the file is not in the first folder, copy it from the second folder
        if not os.path.exists(first_file):
            os.system(f'cp {second_file} {output_file}')
            continue

        # If the file is not in the second folder, copy it from the first folder
        if not os.path.exists(second_file):
            os.system(f'cp {first_file} {output_file}')
            continue

        # If the file is in both folders, merge them
        # Check if the file is a CSV file
        if file.endswith('.csv'):
            with open(first_file, 'r') as f:
                first_data = list(csv.reader(f))
            with open(second_file, 'r') as f:
                second_data = list(csv.reader(f))

            # Merge the data
            result = utils.merge_csv(first_data, second_data)

            # Save the result
            with open(output_file, 'w') as f:
                writer = csv.writer(f)
                writer.writerows(result)
            continue
        elif file.endswith('index.html'):
            with open(first_file, 'r') as f:
                first_data = f.read()
            with open(second_file, 'r') as f:
                second_data = f.read()

            # Merge the data
            result = utils.merge_html(first_data, second_data)

            # Save the result
            with open(output_file, 'w') as f:
                f.write(result)
        else:
            print(f'File type not supported: {file}')

    print('Done!')


if __name__ == '__main__':
    main()

