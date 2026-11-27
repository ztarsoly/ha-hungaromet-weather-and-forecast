"""Log a problem once, and the recovery of a failing download once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger


@dataclass
class FailureRecords:
    """What has been logged, so that it is not logged again.

    Each setup of an entry keeps its own records in its runtime_data.
    """

    # The names of the downloads that are failing
    failing: set[str] = field(default_factory=set)
    # The warnings logged by warn_once, as (message, *arguments)
    warned: set[tuple[object, ...]] = field(default_factory=set)
    # Set once the entry has unloaded: what finishes later is not logged
    closed: bool = False
    # The warnings kept back by hold(), as (logger, message, arguments,
    # exc_info); None while warnings are logged at once
    waiting: list[tuple] | None = None

    def hold(self) -> None:
        """Log warnings at debug level only, and keep them for release().

        For a setup that may be put off: Home Assistant then says why, and the
        integration logs nothing more about it.
        """
        self.waiting = []

    def release(self) -> None:
        """Log the warnings kept back, and the later ones at once."""
        waiting, self.waiting = self.waiting or [], None
        for logger, message, args, exc_info in waiting:
            logger.warning(message, *args, exc_info=exc_info)

    def warn(
        self,
        logger: Logger,
        message: str,
        *args: object,
        exc_info: BaseException | None = None,
    ) -> None:
        """Log a warning, or keep it back."""
        if self.waiting is None:
            logger.warning(message, *args, exc_info=exc_info)
        else:
            logger.debug(message, *args, exc_info=exc_info)
            self.waiting.append((logger, message, args, exc_info))

    def warn_once(self, logger: Logger, message: str, *args: object) -> None:
        """Log a warning the first time it comes up with these arguments."""
        if not self.closed and (message, *args) not in self.warned:
            self.warned.add((message, *args))
            self.warn(logger, message, *args)


class FailureLog:
    """
    Warn at the first failure of a download and when it works again.

    The failures in between are not logged.
    """

    def __init__(
        self, logger: Logger, name: str, records: FailureRecords | None = None
    ) -> None:
        """
        Initialize; name says what is downloaded.

        records: where it is kept whether the download is failing. Logs of the
        same name share that through an entry's records; without them a log
        keeps its own.
        """
        self._logger = logger
        self._name = name
        self.records = records or FailureRecords()

    def failed(self, reason: object, exc_info: BaseException | None = None) -> None:
        """
        Log the reason, unless the download was failing already.

        exc_info: an unexpected error, logged with its traceback.
        """
        if not self.records.closed and self._name not in self.records.failing:
            self.records.failing.add(self._name)
            self.records.warn(
                self._logger,
                "%s failed: %s",
                self._name,
                # A timeout, for one, has no message
                str(reason) or type(reason).__name__,
                exc_info=exc_info,
            )

    def succeeded(self) -> None:
        """Log the recovery of a download that was failing."""
        if not self.records.closed and self._name in self.records.failing:
            self.records.failing.discard(self._name)
            self.records.warn(self._logger, "%s works again", self._name)
