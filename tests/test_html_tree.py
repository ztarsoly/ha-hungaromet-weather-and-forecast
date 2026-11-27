"""Tests for the HTML reader."""

from custom_components.hungaromet_weather_and_forecast.html_tree import parse_html

PAGE = """<!DOCTYPE html>
<html><body>
<script>var tbody = "<td>no text</td>";</script>
<style>td { color: red }</style>
<!-- a comment -->
<table><tbody>
<tr><th class="naptar big"><div> Október </div><div>28.</div></th>
<TD Class="ora" onmouseover="show('&lt;div&gt;', 'a &amp; b')" hidden>07:00<br/>
<img src="/w1.png"> UTC </td></tr>
</tbody></table>
<a href="a.png">A &amp; B</a><a>no link</a>
</body></html>"""


def test_elements_are_found_by_name_class_and_depth():
    """Elements are searched everywhere inside, or among the direct children."""
    page = parse_html(PAGE)
    body = page.find("tbody")
    (row,) = body.find_all("tr", recursive=False)

    assert row.find("th", class_="naptar").classes == ["naptar", "big"]
    assert row.find("th", class_="other") is None
    assert [div.text() for div in row.find_all("div")] == ["Október", "28."]
    assert not row.find_all("div", recursive=False)
    assert [a.get("href", "") for a in page.find_all("a")] == ["a.png", ""]


def test_attributes_are_unescaped_and_named_in_lower_case():
    """Attributes are read like a browser reads them; one without a value is empty."""
    cell = parse_html(PAGE).find("td")

    assert cell.classes == ["ora"]
    assert cell.get("onmouseover") == "show('<div>', 'a & b')"
    assert cell.get("hidden") == ""
    assert cell.get("missing") is None
    assert cell.find("img").get("src") == "/w1.png"


def test_text_leaves_out_scripts_styles_and_comments():
    """The page's text has only what it shows."""
    page = parse_html(PAGE)

    assert page.text(" ") == "Október 28. 07:00 UTC A & B no link"
    assert page.find("td").text() == "07:00UTC"


def test_unclosed_and_stray_tags():
    """An end tag closes what is open inside; a stray one is ignored."""
    page = parse_html("<div><p>one<p>two</div></span>three<td/>four")
    (div,) = page.find_all("div", recursive=False)

    assert div.text(" ") == "one two"
    assert page.text(" ") == "one two three four"
    assert not page.find("td").children


def test_deeply_nested_tags():
    """Thousands of tags left open do not end in a recursion error."""
    page = parse_html("<p>x" * 5000)

    assert len(page.find_all("p")) == 5000
