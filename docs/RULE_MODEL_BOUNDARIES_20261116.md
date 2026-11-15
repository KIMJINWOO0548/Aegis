# Public rule model validation boundary fix

Four concrete failures were reproduced before the fix (four regression tests all
failed). Public Python callers could use Pydantic model_copy(update=...) to obtain
instances that passed isinstance checks but had not been validated:

- both numeric bounds absent could produce WITHIN_LIMITS even for value 999;
- duplicate rule IDs in a configuration reached archive reading/evaluation;
- duplicate rule/version entries in a version lock were accepted;
- an invalid empty configuration version passed lock verification.

Frozen models do not make model_copy updates validated. These APIs now serialize
the provided model to ordinary Python data and validate a fresh nested model before
use. evaluate_rule and review_archive reject invalid rules/configurations before
reading evidence; rule version build/verify revalidate config and lock models.
Valid existing input contracts and explicit version snapshots remain unchanged.
This is a correctness boundary, not a claim to sandbox arbitrary Python code.

Python 3.12.10: the four new regressions plus restoration, rule-version and numeric
rule suites passed **111/111**. Before fixing, **4/4 new tests failed** because the
invalid instances were accepted. Tests use synthetic fixtures and a mocked archive
reader; no user rules, archives or runtime files were changed. No storage/platform
production guard was removed, no running rule activated and main was preserved.

Expanded copied-model cases cover boolean upper bounds, boolean maxAgeMs and
inverted bounds. Final targeted regression: **114/114 PASS** (seven new boundary
cases plus 107 existing tests). This is not a full application regression.
