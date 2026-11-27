"""Write a feed's rows for the tests column by column."""


def table(columns: dict[str, list]) -> list[dict]:
    """Return one row per station from the columns' values."""
    return [dict(zip(columns, values)) for values in zip(*columns.values())]


def column(rows: list[dict], name: str) -> list:
    """Return one column's values."""
    return [row[name] for row in rows]
