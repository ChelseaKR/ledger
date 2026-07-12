# Catalog location

The shipped gettext catalogs live under
[`src/ledger/locales/`](../src/ledger/locales/), inside the Python package so
installed wheels can resolve them at runtime. This root marker makes the catalog
infrastructure discoverable to repository-level standards tooling without
duplicating the catalogs or creating a second source of truth.
