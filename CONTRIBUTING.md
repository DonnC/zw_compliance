# Contributing

Thank you for helping Zimbabwean businesses stay compliant on ERPNext.

## Before you start

- **Bugs:** open an issue with your ERPNext and Frappe versions, the steps, and what you expected. For a ZIMRA rejection, include the error code and colour and the receipt's `payload` from the *Zimra Fiscal Receipt*. Mask the buyer's TIN and the device ID.
- **Features and new pillars** (payroll, NSSA, ZIMDEF…): open an issue first to agree the approach and the legal source (act, statutory instrument, ZIMRA public notice, or API specification).
- **Security issues:** see [SECURITY.md](SECURITY.md). Do not open a public issue.

## Pull requests

1. Branch from `main`. Keep each PR to one change.
2. Follow [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): `pre-commit` and all three test layers must pass.
3. Changes to what is signed, numbered or sent to ZIMRA need:
   - a reference to the specification section;
   - a test, against the specification's own example where one exists;
   - an update to [docs/COMPLIANCE.md](docs/COMPLIANCE.md);
   - a **Compliance** entry in [CHANGELOG.md](CHANGELOG.md).
4. User-visible changes update [docs/USER_GUIDE.md](docs/USER_GUIDE.md). API changes update [docs/API.md](docs/API.md) and keep the response envelope unchanged.
5. Strings shown to users go through `_()` / `__()`.

## Style

- Python: ruff (config in `pyproject.toml`), tabs, as in Frappe. Docstrings say *why*, and cite spec sections.
- Use the framework's own APIs as found in the bench's `frappe` and `erpnext` source, and only those available in both v15 and v16.
- Name things in business language in the UI; ZIMRA's names belong in code and payloads.

## Licence

By contributing you agree that your contribution is licensed under the
project's MIT licence.
