# Test fixtures — synthetic, consented sample records

Everything in this directory is **synthetic**. None of it describes a real
person, and none of it contains real personal data. The fixtures exist only so
the end-to-end / CLI / server tests have small, deterministic payloads to ingest
and a record at each access-policy level to exercise the disclosure boundary.

## Provenance and consent

These files were written by the test author to stand in for the kinds of
community material ledger preserves (oral histories, mutual-aid notes). They are
released for use as test data. There is no contributor to out: where a test
ingests a "contributor identity" it uses a **loud sentinel string**
(`SENTINEL-IDENTITY-DO-NOT-LEAK-...`) that is obviously fake, so that a leak of it
to any read path, log, or on-disk artifact is unmistakable. The sentinel is the
only "identity" these tests ever handle, and it never names a real person.

## The no-outing rule, restated for fixtures

No fixture payload (`*.txt`) carries a contributor name, contact, pronoun, or any
other identifying detail. The contributor's identity, in the tests, lives **only**
in the sentinel that is sealed into the encrypted vault. A fixture payload is
benign, collection-level content that is safe to render on the public site.

## One payload per access-policy level

`ledger.models.AccessPolicy` defines five disclosure levels. There is one tiny
payload file per level so a test can ingest a record at each level and assert the
disclosure boundary behaves correctly (public material is shown to everyone;
sealed material is shown only to a steward / on its unseal date / when its
condition is met):

| File                      | Intended access policy      | What it stands for                          |
| ------------------------- | --------------------------- | ------------------------------------------- |
| `public.txt`              | `public`                    | Material safe for the open public site.     |
| `community.txt`           | `community`                 | Material for authenticated community members.|
| `stewards.txt`            | `stewards`                  | Material only stewards administer.          |
| `sealed_until.txt`        | `sealed-until`              | Material sealed until a future date.        |
| `sealed_conditional.txt`  | `sealed-conditional`        | Material sealed until a named condition.    |

The mapping from file to policy is *intent*, declared here for the reader; the
tests attach the policy explicitly when they ingest each fixture (the bytes
themselves carry no policy). Keeping the payloads tiny keeps ingest fast and the
fixtures easy to eyeball.
