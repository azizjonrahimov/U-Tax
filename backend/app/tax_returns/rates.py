"""D42: every rate and threshold the engine uses, with its source. Checked 2026-11-08.

Change a number only together with its source line. The engine never invents a rate: a state not
in STATES is "not covered" and goes to a human.
"""
FEDERAL = {
    "rate": 0.21,  # IRC §11(b): flat 21% on taxable income (TCJA, unchanged for 2025/2026)
    "form": "Form 1120", "due": "April 15 (15th day of the 4th month after year end)",
    "source": "IRC §11(b); IRS Form 1120 (2025) and its instructions",
}

# NY fixed dollar minimum, general corporations (CT-3-I 2025, tax rates schedule)
NY_FDM = [(100_000, 25), (250_000, 75), (500_000, 175), (1_000_000, 500), (5_000_000, 1_500),
          (25_000_000, 3_500), (50_000_000, 5_000), (100_000_000, 10_000),
          (250_000_000, 20_000), (500_000_000, 50_000), (1_000_000_000, 100_000)]
NY_FDM_TOP = 200_000

STATES = {
    "CA": {"name": "California", "kind": "income", "rate": 0.0884, "minimum": 800,
           "form": "Form 100", "due": "April 15",
           "source": "FTB: C corporations, 8.84% and the $800 minimum franchise tax "
                     "(no minimum in the first taxable year); Tax Foundation 2025 table"},
    "NY": {"name": "New York", "kind": "ny", "rate": 0.065, "rate_over_5m": 0.0725,
           "form": "Form CT-3", "due": "April 15",
           "source": "NY CT-3-I (2025) tax rates schedule: 6.5%, 7.25% over $5M business "
                     "income base; capital base 0 for small business taxpayers; FDM table"},
    "TX": {"name": "Texas", "kind": "tx", "no_tax_due": 2_650_000, "rate": 0.0075,
           "rate_retail": 0.00375, "ez_rate": 0.00331, "ez_limit": 20_000_000,
           "form": "Franchise Tax Report (05-158) or No Tax Due + Public Information Report",
           "due": "May 15",
           "source": "Texas Comptroller, Franchise Tax: $2,650,000 no-tax-due threshold "
                     "(2026 and 2027 reports), 0.75% / 0.375% retail-wholesale, EZ 0.331% "
                     "up to $20M revenue"},
    "FL": {"name": "Florida", "kind": "income", "rate": 0.055, "exemption": 50_000,
           "form": "Form F-1120", "due": "May 1 (1st day of the 5th month)",
           "source": "Florida DOR: 5.5% on income above the $50,000 exemption; "
                     "Tax Foundation 2025 table"},
    "IL": {"name": "Illinois", "kind": "income", "rate": 0.095,
           "form": "Form IL-1120", "due": "April 15",
           "source": "Illinois: 7% income tax + 2.5% personal property replacement tax "
                     "(Tax Foundation 2025: 9.5%)"},
    "DE": {"name": "Delaware", "kind": "income", "rate": 0.087,
           "form": "Form 1100", "due": "April 15",
           "source": "Delaware: 8.7% corporate income tax (Tax Foundation 2025)"},
    "WY": {"name": "Wyoming", "kind": "none", "form": None, "due": None,
           "source": "Tax Foundation 2025: no corporate income or gross receipts tax"},
    "SD": {"name": "South Dakota", "kind": "none", "form": None, "due": None,
           "source": "Tax Foundation 2025: no corporate income or gross receipts tax"},
    "NV": {"name": "Nevada", "kind": "gross_receipts", "form": None, "due": None,
           "source": "Tax Foundation 2025: gross receipts tax instead of income tax"},
    "WA": {"name": "Washington", "kind": "gross_receipts", "form": None, "due": None,
           "source": "Tax Foundation 2025: gross receipts tax instead of income tax"},
    "OH": {"name": "Ohio", "kind": "gross_receipts", "form": None, "due": None,
           "source": "Tax Foundation 2025: gross receipts tax instead of income tax"},
}
