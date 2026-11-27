"""A small reader of met.hu's HTML pages, on Python's own HTML parser.

It builds a tree of elements to search. An end tag closes the nearest open
element of its name, with everything left open inside it; one without such an
element is ignored.
"""

from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser

# Elements that never have content or an end tag
VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "keygen", "link",
    "meta", "param", "source", "track", "wbr",
})
# Elements whose content is not text of the page
HIDDEN_ELEMENTS = frozenset({"script", "style", "template"})


class Element:
    """An element of a page, with its attributes and content."""

    def __init__(self, name: str, attributes: dict[str, str] | None = None) -> None:
        self.name = name
        self.attributes = attributes or {}
        # The elements and texts inside, in the page's order
        self.children: list[Element | str] = []

    def get(self, attribute: str, default: str | None = None) -> str | None:
        """Return an attribute's value; names are in lower case."""
        return self.attributes.get(attribute, default)

    @property
    def classes(self) -> list[str]:
        """Return the element's classes, in their order."""
        return self.attributes.get("class", "").split()

    def _descendants(self) -> Iterator[Element | str]:
        """Yield everything inside the element, in the page's order."""
        # Without recursion: tags a page leaves open can nest very deeply
        waiting = self.children[::-1]
        while waiting:
            node = waiting.pop()
            yield node
            if isinstance(node, Element):
                waiting.extend(node.children[::-1])

    def find_all(
        self, name: str, class_: str | None = None, recursive: bool = True
    ) -> list[Element]:
        """Return the elements of a name (and class) inside, or just directly in."""
        nodes = self._descendants() if recursive else self.children
        return [
            node
            for node in nodes
            if isinstance(node, Element)
            and node.name == name
            and (class_ is None or class_ in node.classes)
        ]

    def find(self, name: str, class_: str | None = None) -> Element | None:
        """Return the first element of a name (and class) inside."""
        return next(iter(self.find_all(name, class_)), None)

    def text(self, separator: str = "") -> str:
        """Return the texts inside, each trimmed, without the empty ones."""
        texts = (node.strip() for node in self._descendants() if isinstance(node, str))
        return separator.join(text for text in texts if text)


class _TreeBuilder(HTMLParser):
    """Build the elements as Python's parser reports a page."""

    def __init__(self) -> None:
        super().__init__()
        self.root = Element("")
        self._open = [self.root]
        # The pieces of the text being read; the parser can report one in parts
        self._text: list[str] = []

    def _end_text(self) -> None:
        if self._text:
            self._open[-1].children.append("".join(self._text))
            self._text = []

    def _add(self, tag: str, attrs: list[tuple[str, str | None]]) -> Element:
        self._end_text()
        element = Element(tag, {name: value or "" for name, value in attrs})
        self._open[-1].children.append(element)
        return element

    def handle_starttag(self, tag, attrs):
        element = self._add(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self._open.append(element)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        self._end_text()
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].name == tag:
                del self._open[index:]
                return

    def handle_data(self, data):
        if not any(element.name in HIDDEN_ELEMENTS for element in self._open):
            self._text.append(data)

    def handle_comment(self, data):
        self._end_text()

    def close(self):
        super().close()
        self._end_text()


def parse_html(html: str) -> Element:
    """Return a page as an element holding all of it."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root
