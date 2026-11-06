# Backup slide (if everything live breaks)

## U-Tax: an accounting firm run by 7 AI agents and 1 human

- **What it does:** free Delaware franchise tax checkups for hackathon startups. The authorized
  shares method can show $85,165 for 10M shares; the assumed par value method is often $400 plus
  the $50 annual report fee.
- **Who works there:** Chief of Staff, Intake, Tax, QA, Accounting, Marketing, HR, all on Naïve.
  One human CEO approves the first of each kind of action, makes the hire, and otherwise logs in.
- **Why you can trust the numbers:** the AI only quotes where each number is in the client's
  message. Tested code parses and computes, and a QA agent re-checks every quote before a memo goes out.
- **Why you can see it:** every agent action is an event in an append-only log, and the Agent
  Theater replays the log as named cursors. Nothing is animated that didn't happen.
- **Last night, from the log:** N clients · K human touches (4 training) · clients saved $X ·
  the firm spent $Y (estimated)

*Fill N, K, $X and $Y from the theater's bottom bar or `/api/state` right before presenting. Never
type them from memory.*

Educational estimates, not tax advice. U-Tax files nothing and never asks for an SSN or EIN.
