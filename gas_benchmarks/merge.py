from __future__ import annotations

from typing import Iterable, List

from bs4 import BeautifulSoup


def merge_csv(first_data: List[List[str]], second_data: List[List[str]]) -> List[List[str]]:
    """
    Merge two CSV tables by keeping the header from the first file.
    """
    if not first_data:
        return second_data
    headers = first_data[0]
    merged = [headers]
    merged.extend(first_data[1:])
    merged.extend(second_data[1:])
    return merged


def merge_html(first_html: str, second_html: str) -> str:
    """
    Merge HTML tables with matching ``id`` attributes by appending rows.
    """
    first_soup = BeautifulSoup(first_html, "html.parser")
    second_soup = BeautifulSoup(second_html, "html.parser")

    for table in first_soup.find_all("table"):
        table_id = table.get("id")
        if not table_id:
            continue
        other_table = second_soup.find("table", {"id": table_id})
        if other_table is None:
            continue

        first_body = table.find("tbody")
        other_body = other_table.find("tbody")
        if first_body is None or other_body is None:
            continue

        for row in other_body.find_all("tr"):
            first_body.append(row)

    return first_soup.prettify()

