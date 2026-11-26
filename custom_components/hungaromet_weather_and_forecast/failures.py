"""Log a lost download once, and its recovery once."""

import logging


class FailureLog:
    """Warn at the first failure of a download and when it works again.

    The failures in between are not logged.
    """

    def __init__(self, logger: logging.Logger, name: str) -> None:
        self._logger = logger
        self._name = name
        self._failing = False

    def failed(self, reason: object) -> None:
        """Log the reason, unless the download was failing already."""
        if not self._failing:
            self._failing = True
            self._logger.warning("%s failed: %s", self._name, reason)

    def succeeded(self) -> None:
        """Log the recovery of a download that was failing."""
        if self._failing:
            self._failing = False
            self._logger.warning("%s works again", self._name)
