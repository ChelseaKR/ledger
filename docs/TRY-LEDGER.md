# Try ledger in five minutes

This walkthrough gives a prospective community, archivist, or reviewer a small,
repeatable way to see ledger's core preservation and disclosure path. It uses only
synthetic data created at run time. It does **not** need an account, a networked
service, or a real contributor.

## What you will see

The executable demo does five concrete things:

1. Creates a temporary archive with the narrowest disclosure default.
2. Ingests a synthetic oral history and stores a synthetic contributor identity in
   the encrypted identity vault, separate from the record.
3. Replicates the BagIt bag to a second local location and verifies its fixity.
4. Serves the record locally, then checks the HTML, JSON, health endpoint, request
   log, and on-disk metadata for an identity or sealed-field leak.
5. Records a consent change and verifies that it removes the record from anonymous
   browsing.

The run leaves its temporary directory printed in the terminal so a reviewer can
inspect the synthetic artifacts. Do not substitute real names or records for the
synthetic values.

## Run it

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/ChelseaKR/ledger.git
cd ledger
make install
make demo
```

The important success lines are:

```text
PASS: contributor identity absent from every public surface
```

and the summary stating that the synthetic bag was replicated and verified and that
the consent change tightened disclosure. A non-zero exit is a failed proof, not an
inconclusive result.

## Interpret the result correctly

A passing demo is evidence for the specific exercised properties: the checked
synthetic identity does not leak through the tested surfaces, a copy verifies against
its fixity data, and the exercised consent change affects anonymous disclosure. It is
not evidence that:

- ledger is safe for real records or for every threat model;
- sealed content is encrypted at rest (it is not by default; see
  [the adoption checklist](ADOPTING.md));
- an independent security, cryptography, legal, or accessibility review has happened;
- an archive has community governance, full-disk encryption, TLS, off-box replicas,
  or a safe key-custody arrangement.

Those are adoption and human-review questions. The next honest step is the
[community-archivist pilot packet](reviews/community-archivist-pilot.md), followed
by the [adoption checklist](ADOPTING.md) before any real record is considered.

## Browse a synthetic archive locally

For an interactive look at the interface, the browser accessibility harness can seed
a synthetic archive and serve it on loopback:

```sh
cd tools/a11y_browser
python -m serve_demo
```

Open the local address it prints (normally `http://127.0.0.1:8099`). The seeded
records, grants, and identities are synthetic. Stop the server with `Ctrl-C` when
finished.

The [AWS showcase runbook](../infra/aws/README.md) describes an optional public,
synthetic-only demonstration deployment. It creates billable infrastructure and
requires a domain, so it is deliberately an operator decision rather than a default
quickstart.
