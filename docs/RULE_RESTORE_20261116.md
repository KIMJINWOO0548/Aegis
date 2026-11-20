# Explicit historical rule restoration proposal

## Gap addressed

Existing `rule_versions.py` builds and verifies immutable rule ID/version references
and retains historical snapshots. It did not expose an operator selection workflow
that reconstructs a configuration from those retained versions and explains its
effect before activation. This adds the safe offline portion of that workflow.
It does not change storage gates, collection or running equipment configuration.

`restore_rule_versions(current, lock, selections, config_version=...)` revalidates
both models, verifies the current configuration against its reference, and accepts
only explicit current rule IDs and registered historical versions. It preserves
unselected rules and creates a separately versioned proposed configuration. The
result includes before/after hashes, changed field names, explicit targetChanged,
source config hash and lock hash. A target rollback is visible, never silent.
Unknown rule/version, no-op selection, reused current configuration version,
modified registered snapshots and mismatched scopes are rejected.

The result always says activationStatus NOT_APPLIED and evaluationMode
OFFLINE_REVIEW_ONLY. Input objects are not mutated. There is no configuration write,
live reload, rule activation, equipment action or automatic rollback. Validation
of a proposal does not establish that the historical bounds are operationally safe.
Configuration versions are identifiers, not numeric ordering; only inequality with
the current version is enforced. Detecting reuse of a different historical config
version requires a future configuration-level history/activation registry.

## CLI

```text
python -m aegis.rule_restore --config current.json --lock versions.json \
  --select numeric.bounds=1 --new-config-version 3
```

Repeat --select for multiple rules. Duplicate selections are rejected. The complete
bounded proposal is emitted to stdout only after validation; failure emits a code
to stderr. Use proposedConfig as a review artifact, not a command to replace the
current file. File loading inherits existing rule/config loader assumptions and
is not hardened against a hostile parent directory. This does not supply atomic
publication or recovery of an active configuration file.

## Verification

Python 3.12.10: `test_rule_restore.py`, `test_rule_versions.py`, and
`test_limit_rules.py`: **107/107 PASS**. Ten new cases cover historical restoration,
input preservation, unknown/no-op/invalid selections, explicit target changes,
unselected-rule preservation, tampered lock/current conflicts, stdout-only CLI
output and duplicate selection errors. No user configuration was read or modified.
Production rule deployment, UI workflow and rule-state persistence remain separate
unimplemented/unchecked gates, not implied by these offline tests.

Expanded adjacent regression including rule-version CLI and numeric-limit CLI:
**167/167 PASS** in 83.14 seconds on Python 3.12.10. PYTHONPATH=src was set for
this process so existing subprocess CLI tests could import this checkout; no
persistent environment settings were changed.

Independent review reran the core three suites: **107/107 PASS** and no scoped
blocker. It confirmed input/reference revalidation, scope and immutable-version
checks, unselected-rule preservation, explicit target changes and NOT_APPLIED /
stdout-only boundaries. Historical configuration-version reuse and active
configuration deployment remain outside this proposal workflow as stated above.
