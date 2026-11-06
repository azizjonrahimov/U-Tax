"""Engine errors. The gateway maps each to a client-facing action (Error & Rescue Map)."""


class EngineError(Exception):
    """Base class for every engine error."""


class ParseError(EngineError):
    """A quoted span could not be turned into exactly one number."""

    def __init__(self, span: str, reason: str) -> None:
        super().__init__(f"{reason}: {span!r}")
        self.span = span
        self.reason = reason


class ValidationError(EngineError):
    """Inputs parse but cannot be used (negative, issued > authorized, zero issued...)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class MissingFactError(EngineError):
    """A fact the calculation needs was not provided; Intake should ask for it."""

    def __init__(self, fact: str) -> None:
        super().__init__(f"missing fact: {fact}")
        self.fact = fact
