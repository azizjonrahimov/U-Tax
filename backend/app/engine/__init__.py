"""Pure tax engine: no I/O, exact arithmetic, integer cents (math lives in tested code)."""

from app.engine.errors import EngineError, MissingFactError, ParseError, ValidationError
from app.engine.form5472 import form5472_flag
from app.engine.franchise import (
    DEFAULT_STATED_PAR,
    DUE_DATE,
    DUE_DATE_TEXT,
    ENGINE_VERSION,
    FeeQuote,
    FranchiseResult,
    assumed_par_method_cents,
    authorized_shares_method_cents,
    compute_franchise_tax,
)
from app.engine.money import format_usd
from app.engine.parse_amount import parse_amount, parse_dollars_to_cents, parse_par, parse_shares
from app.engine.rnd import rnd_flag

__all__ = [
    "DEFAULT_STATED_PAR",
    "DUE_DATE",
    "DUE_DATE_TEXT",
    "ENGINE_VERSION",
    "EngineError",
    "FeeQuote",
    "FranchiseResult",
    "MissingFactError",
    "ParseError",
    "ValidationError",
    "assumed_par_method_cents",
    "authorized_shares_method_cents",
    "compute_franchise_tax",
    "form5472_flag",
    "format_usd",
    "parse_amount",
    "parse_dollars_to_cents",
    "parse_par",
    "parse_shares",
    "rnd_flag",
]
