import argparse
import json
import os

import yaml
from bs4 import BeautifulSoup
import utils
import csv


def get_html_report(client_results, clients, results_paths, test_cases, methods, gas_set, metadata, images):
    # Load the computer specs
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text

    results_to_print = ('<!DOCTYPE html\>' +
                        '<html lang="en">' +
                        '<head>' +
                        '    <meta charset=\"UTF-8\">' +
                        '    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">' +
                        '    <title>Benchmarking Report</title>' +
                        '    <style>' +
                        '        body {' +
                        '            font-family: Arial, sans-serif;' +
                        '        }' +
                        '        table {' +
                        # '            width: 100%;' +
                        '            border-collapse: collapse;' +
                        '            margin-bottom: 20px;' +
                        '        }' +
                        '        th, td {' +
                        '            border: 1px solid #ddd;' +
                        '            padding: 8px;' +
                        '            text-align: center;' +
                        '        }' +
                        '        th {' +
                        '            background-color: #f2f2f2;' +
                        # '            cursor: pointer;' +
                        '        }' +
                        '        .title {' +
                        '            text-align: left;' +
                        '        }' +
                        '        .preserve-newlines {' +
                        '            white-space: pre-wrap;' +
                        '        }' +
                        '    </style>' +
                        '</head>' +
                        '<body>'
                        '<h2>Computer Specs</h2>'
                        '<pre">' + computer_spec + '</pre>')
    csv_table = {}
    for client in clients:
        image_to_print = ''
        image_json = json.loads(images)
        if client in image_json:
            if image_json[client] != 'default' and image_json[client] != '':
                image_to_print = image_json[client]
        if image_to_print == '':
            with open('images.yaml', 'r') as f:
                el_images = yaml.safe_load(f)["images"]
            client_without_tag = client.split("_")[0]
            image_to_print = el_images[client_without_tag]
        results_to_print += f'<h1>{client.capitalize()} - {image_to_print} - Benchmarking Report</h1>' + '\n'
        results_to_print += f'<table id="table_{client}">'
        results_to_print += ('<thread>\n'
                             '<tr>\n'
                             f'<th class=\"title\" onclick="sortTable(0, \'table_{client}\', false)" style="cursor: pointer;">Title &uarr; &darr;</th>\n'
                             f'<th onclick="sortTable(1, \'table_{client}\', true)" style="cursor: pointer;">Max (MGas/s) &uarr; &darr;</th>\n'
                             f'<th onclick="sortTable(2, \'table_{client}\', true)" style="cursor: pointer;">p50 (MGas/s) &uarr; &darr;</th>\n'
                             f'<th onclick="sortTable(3, \'table_{client}\', true)" style="cursor: pointer;">p95 (MGas/s) &uarr; &darr;</th>\n'
                             f'<th onclick="sortTable(4, \'table_{client}\', true)" style="cursor: pointer;">p99 (MGas/s) &uarr; &darr;</th>\n'
                             f'<th onclick="sortTable(5, \'table_{client}\', true)" style="cursor: pointer;">Min (MGas/s) &uarr; &darr;</th>\n'
                             '<th>N</th>\n'
                             '<th class=\"title\">Description</th>\n'
                             '</tr>\n'
                             '</thread>\n'
                             '<tbody>\n')
        gas_table_norm = utils.get_gas_table(client_results, client, test_cases, gas_set, methods[0], metadata)
        csv_table[client] = gas_table_norm
        for test_case, data in gas_table_norm.items():
            results_to_print += (f'<tr>\n<td class="title">{data[0]}</td>\n'
                                 f'<td>{data[2]}</td>\n'
                                 f'<td>{data[3]}</td>\n'
                                 f'<td>{data[4]}</td>\n'
                                 f'<td>{data[5]}</td>\n'
                                 f'<td>{data[1]}</td>\n'
                                 f'<td>{data[6]}</td>\n'
                                 f'<td style="text-align:left;" >{data[7]}</td>\n</tr>\n')
        results_to_print += '\n'
        results_to_print += ('</table>\n'
                             '</tbody>\n')

    results_to_print += ('    <script>'
                         'function sortTable(n, table_name, nm) {'
                         '  var table, rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;'
                         '  table = document.getElementById(table_name);'
                         '  switching = true;'
                         '  dir = "asc";'
                         '  while (switching) {'
                         '    switching = false;'
                         '    rows = table.rows;'
                         '    for (i = 1; i < (rows.length - 1); i++) {'
                         '      shouldSwitch = false;'
                         '      x = rows[i].getElementsByTagName("TD")[n];'
                         '      y = rows[i + 1].getElementsByTagName("TD")[n];'
                         '      if (dir == "asc") {'
                         '        if (nm) {'
                         '          if (Number(x.innerHTML) > Number(y.innerHTML)) {'
                         '            shouldSwitch = true;'
                         '            break;'
                         '          }'
                         '        } else {'
                         '          if (x.innerHTML.toLowerCase() > y.innerHTML.toLowerCase()) {'
                         '            shouldSwitch = true;'
                         '            break;'
                         '          }'
                         '        }'
                         '      } else if (dir == "desc") {'
                         '        if (nm) {'
                         '          if (Number(x.innerHTML) < Number(y.innerHTML)) {'
                         '            shouldSwitch = true;'
                         '            break;'
                         '          }'
                         '        } else {'
                         '          if (x.innerHTML.toLowerCase() < y.innerHTML.toLowerCase()) {'
                         '            shouldSwitch = true;'
                         '            break;'
                         '          }'
                         '        }'
                         '      }'
                         '    }'
                         '    if (shouldSwitch) {'
                         '      rows[i].parentNode.insertBefore(rows[i + 1], rows[i]);'
                         '      switching = true;'
                         '      switchcount ++;'
                         '    } else {'
                         '      if (switchcount == 0 && dir == "asc") {'
                         '        dir = "desc";'
                         '        switching = true;'
                         '      }'
                         '    }'
                         '  }'
                         '}'
                         '</script>'
                         '</body>'
                         '</html>')

    soup = BeautifulSoup(results_to_print, 'lxml')
    formatted_html = soup.prettify()
    print(formatted_html)
    with open(f'{results_paths}/reports/index.html', 'w') as file:
        file.write(formatted_html)

    for client, gas_table in csv_table.items():
        with open(f'{results_paths}/reports/output_{client}.csv', 'w', newline='') as csvfile:
            # Create a CSV writer object
            csvwriter = csv.writer(csvfile)
            csvwriter.writerow(
                ['Title', 'Max (MGas/s)', 'p50 (MGas/s)', 'p95 (MGas/s)', 'p99 (MGas/s)', 'Min (MGas/s)', 'N',
                 'Description'])
            for test_case, data in gas_table.items():
                csvwriter.writerow([data[0], data[2], data[3], data[4], data[5], data[1], data[6], data[7]])


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--testsPath', type=str, help='results', default='tests/')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to compare, '
                                                    'split them by comma, ex: nethermind,geth',
                        default='nethermind,geth,reth')
    parser.add_argument('--runs', type=int, help='Number of runs the program will process', default='10')
    parser.add_argument('--images', type=str, help='Image values per each client',
                        default='{ "nethermind": "default", "besu": "default", "geth": "default", "reth": "default" , '
                                '"erigon": "default"}')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    clients = args.clients
    tests_path = args.testsPath
    runs = args.runs
    images = args.images

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text
    print(computer_spec)

    client_results = {}
    failed_tests = {}
    methods = ['engine_newPayloadV3']
    fields = 'max'

    test_cases = utils.get_test_cases(tests_path)
    for client in clients.split(','):
        client_results[client] = {}
        failed_tests[client] = {}
        for test_case_name, test_case_gas in test_cases.items():
            client_results[client][test_case_name] = {}
            failed_tests[client][test_case_name] = {}
            for gas in test_case_gas:
                client_results[client][test_case_name][gas] = {}
                failed_tests[client][test_case_name][gas] = {}
                for method in methods:
                    client_results[client][test_case_name][gas][method] = []
                    failed_tests[client][test_case_name][gas][method] = []
                    for run in range(1, runs + 1):
                        responses, results = utils.extract_response_and_result(results_paths, client, test_case_name,
                                                                               gas, run, method, fields)
                        client_results[client][test_case_name][gas][method].append(results)
                        failed_tests[client][test_case_name][gas][method].append(not responses)

    gas_set = set()
    for test_case_name, test_case_gas in test_cases.items():
        for gas in test_case_gas:
            if gas not in gas_set:
                gas_set.add(gas)

    if not os.path.exists(f'{results_paths}/reports'):
        os.makedirs(f'{results_paths}/reports')

    metadata = {}
    if os.path.exists(f'{tests_path}/metadata.json'):
        data = json.load(open(f'{tests_path}/metadata.json', 'r'))
        for item in data:
            metadata[item['Name']] = item

    get_html_report(client_results, clients.split(','), results_paths, test_cases, methods, gas_set, metadata, images)

    print('Done!')


if __name__ == '__main__':
    main()
