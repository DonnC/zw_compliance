# Development

## Setup

```bash
cd ~/frappe-bench
bench get-app <path-or-url-of-zw_compliance>
bench --site dev.local install-app zw_compliance
bench --site dev.local set-config allow_tests true
bench setup requirements --dev    # pytest
```

Work on a branch. Run `pre-commit install` once: ruff (lint, import order,
format) and prettier run on commit.

## Layout and rules of the road

See [ARCHITECTURE.md](ARCHITECTURE.md). In short:

- `fdms/` must never import Frappe. CI tests it in a bare virtualenv.
- Anything that changes what is signed or sent to ZIMRA needs:
  - a spec reference in the code (`# §13.2.1`);
  - a test, ideally against an example from the specification;
  - an entry in [COMPLIANCE.md](COMPLIANCE.md).
- Frappe and ERPNext APIs: use what exists in **v15 and later** (CI tests v16). Read the framework source in your bench rather than relying on memory or blog posts.
- Custom fields are defined in code (`fiscalisation/setup/custom_fields.py`), each with `module = "ZIMRA Fiscalisation"`. Never export them as fixtures.
- Fiscal records (`Zimra Fiscal Receipt`, `Zimra Fiscal Day`) are written only by code, with `ignore_permissions`. Never add write permissions to them.

## Tests

### Protocol library (seconds, no site)

```bash
cd apps/zw_compliance
python -m pytest
```

Covers:
- the specification's worked examples (`test_spec_vectors.py`);
- vectors from the zimra-fdms SDK that were verified against ZIMRA's live test environment (`test_sdk_vectors.py`, vectors in `fdms/tests/zimra-fdms-vectors.json`);
- receipt rules, money, wire format, client, signing and error catalogue.

### Integration (a minute, needs a site)

```bash
bench --site dev.local run-tests --app zw_compliance
bench --site dev.local run-tests --app zw_compliance --module zw_compliance.fiscalisation.tests.test_rules
```

The tests use their own company (`_Test ZW Company`) and run against
`tests/simulator.py`, an in-process FDMS ported from the zimra-fdms SDK
simulator. It has:
- a real CA that issues device certificates from the app's CSR and authenticates every call by certificate;
- the SDK's fault model (dropped connections, 503s, lost answers, clock skew);
- spec §8.2.1 validation;
- asynchronous closes (`FakeFdms.settle()`).

`FiscalisationTestCase` turns `frappe.db.commit` into a no-op for the class,
so everything, including the sync job's commits, is rolled back afterwards.

Tests within a class share database state. A test that leaves a day closing
or a receipt Red affects the next one; give such tests their own class.

Useful hooks:
- `frappe.flags.zimra_transport_factory(device, mtls)`: inject any `Transport`;
- `self.sync()`: run the sync job inline;
- `self.fdms.faults`: inject `drop_connections`, `server_errors`, `drop_after_submit`, `date_skew_ms`, `refuse_close`.

### End to end (real HTTPS, mutual TLS)

```bash
npx --yes -p zimra-fdms@0.4.1 zimra-fdms-simulator --port 8443 --ca /tmp/zimra-sim-ca.pem &
bench --site dev.local execute zw_compliance.fiscalisation.tests.e2e.run \
  --kwargs "{'url': 'https://127.0.0.1:8443', 'ca': '/tmp/zimra-sim-ca.pem'}"
```

The run registers a device and goes through two fiscal days, the second with
a credit note. It prints `PASSED` and lists the simulator's known
divergences from the spec (see [COMPLIANCE.md](COMPLIANCE.md#simulator-divergences)).
It rolls everything back.

### Demo data

```bash
bench --site dev.local execute zw_compliance.fiscalisation.demo.seed --kwargs "{'days': 21}"
bench --site dev.local execute zw_compliance.fiscalisation.demo.wipe
```

`fiscalisation/demo.py` runs the real flow (ERPNext submit → outbox → sync)
against `tests/simulator.py`. It replays past days through
`frappe.flags.zimra_clock`, the only clock override in the app, and syncs
inline (`frappe.flags.zimra_sync_inline`). The device is flagged `is_demo`:
`connect()` refuses it and the scheduler skips it. `wipe()` deletes the demo
company's records directly, since the guards forbid deleting fiscal records.
`test_demo.py` checks that seed → wipe → seed leaves nothing behind.

### ZIMRA test environment

1. Create a device with environment *Test*, using a test device ID from ZIMRA.
2. Leave *API URL Override* empty: the app uses `https://fdmsapitest.zimra.co.zw`.
3. Run the onboarding steps from the [User guide](USER_GUIDE.md#2-register-the-device).

## CI

`.github/workflows/ci.yml` is **manual for now**: start it from *Actions → CI →
Run workflow* (the automatic triggers are kept, commented out, in the file). It
runs:
- ruff;
- the protocol tests on Python 3.14;
- install, the integration tests, an idempotent re-migrate and the end-to-end
  run, on ERPNext `version-16` (Python 3.14, Node 24).

`bench run-tests --app zw_compliance` runs only this app's `before_tests`
hooks, so `zw_compliance.testing.before_tests` calls ERPNext's own: it
completes the setup wizard on a fresh site (warehouse types, customer groups,
territories, price lists) and does nothing on a site already set up.

## Releasing

1. Update [CHANGELOG.md](../CHANGELOG.md) and `zw_compliance/__init__.py`.
2. Tag `vX.Y.Z`.
3. Any change to signing or numbering must be called out under **Compliance** in the changelog.
