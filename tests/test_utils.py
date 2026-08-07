from bs4 import BeautifulSoup

import utils


def _report(client, values):
    rows = "".join(f"<tr><td>{client}</td><td>{v}</td></tr>" for v in values)
    return (
        "<html><body>"
        f'<table id="table_{client}">'
        "<thead><tr><th>client</th><th>MGas/s</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table></body></html>"
    )


def test_merge_html_stacks_rows_under_one_header():
    # Same client's results from two report folders (same table id). Previously
    # this raised IndexError (it searched for a <thread> tag that never exists),
    # and even after that it nested <tr> inside <tr>. It should now stack the
    # rows under a single header, producing valid HTML.
    first = _report("nethermind", ["101.2", "98.7"])
    second = _report("nethermind", ["100.1", "99.4"])

    merged = utils.merge_html(first, second)
    soup = BeautifulSoup(merged, "html.parser")
    table = soup.find("table")

    # exactly one header, not duplicated
    assert len(table.find_all("thead")) == 1
    # all four data rows are present, stacked in the body
    body = table.find("tbody")
    data_rows = body.find_all("tr")
    assert len(data_rows) == 4
    # no <tr> is nested inside another <tr> (valid table structure)
    for row in table.find_all("tr"):
        assert row.find("tr") is None
    # values from both reports survived
    text = table.get_text()
    for v in ("101.2", "98.7", "100.1", "99.4"):
        assert v in text


def test_merge_html_handles_table_without_thead():
    first = '<html><body><table id="t"><tbody><tr><td>a</td></tr></tbody></table></body></html>'
    second = '<html><body><table id="t"><tbody><tr><td>b</td></tr></tbody></table></body></html>'
    merged = utils.merge_html(first, second)  # must not crash without a <thead>
    assert "a" in merged
    assert "b" in merged
