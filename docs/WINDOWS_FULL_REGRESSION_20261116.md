# Windows full regression classification (2026-11-16)

## Reproducible evidence

Revision `6e443eb80e1453438c25880a7fca42ea3c335413`, Python 3.12.10.
Command: `python -m pytest -q --tb=short --junitxml=<persistent-results.xml>`.
Redirect stdout/stderr to a persistent directory outside command-scoped TEMP.
The full run completed: **298 failed, 966 passed, 11 skipped, 30 errors**, two
warnings, 363.34 seconds. This is NOT a green full regression or Windows production
support claim. The earlier run had 964 passes because it started before the final
two replay tests were added. No failed tests were removed, skipped or xfailed.

`evidence/windows-full-regression-20261116.json` lists every failing, errored and
skipped test by name with its category and the SHA-256/size of both raw artifacts.
The full raw log and JUnit are retained locally (not published, as they include
machine-specific paths). Generated tracked evidence was backed up outside this
repository, then restored; it was not mixed into this report.

## Classification

| Category | Failures | Setup errors | Evidence |
| --- | ---: | ---: | --- |
| Explicit PLATFORM_UNVERIFIED | 273 | 30 | JUnit exception/assertion text; guards remain closed |
| Guard prevents later fault injection | 5 | 0 | Source/test control flow reviewed, details below |
| Windows link permissions | 15 | 0 | WinError 5 or 1314 while creating test fixtures |
| POSIX FIFO fixture unavailable | 3 | 0 | os.mkfifo does not exist on Windows |
| Test harness portability defects | 2 | 0 | Independently reproduced and fixed below |

Five indirect guard failures: backup race, backup stdout-after-publication and SQL
progress tests cannot reach link/publication/progress callbacks because `_create`
rejects Windows first. The two ingest close-after-commit injections run during
InputArchive constructor cleanup after PLATFORM_UNVERIFIED, before any commit;
the injected cleanup error produces INVALID_INPUT, not the expected post-commit
STORE_RESULT_UNKNOWN. These five tests do not demonstrate broken publication or
commit atomicity on the supported platform, nor do they verify those paths on
Windows. A supported POSIX run is still required; the guards were not bypassed.

The 15 permission failures are fixture creation, not observed unsafe link following.
The Windows journal hardlink test separately passed via the normal approved execution
path in the prior focused run. Symlink privilege was NOT enabled; the other permission
cases have not all been rerun outside the sandbox. No socket WinError 10014 occurred
in this particular full run. Do not generalize this to the intermittent issue being
resolved.

## Corrected harness defects

1. PowerShell subprocess stdout inherited the Windows code page while Python decoded
   UTF-8. The real Korean-path test failed with UnicodeDecodeError in the reader
   thread, then json.loads(None). The test's child PowerShell now explicitly emits
   UTF-8. It still evaluates the real verify.ps1 AST and checks the exact argument
   array, including one intact Unicode/space-containing JUnit path.
2. The history boundary fixture used `?` in a Windows filename and SQLite could not
   open it. Windows now uses a legal Unicode/space/# filename; POSIX retains the
   original `#?` path to test URI escaping. All half-open timestamps, original values
   and completeness assertions remain unchanged.

Both defects were seen failing before edits. After correction, the real delivery
and raw-history suites passed **41/41**, 4.70 seconds. Independent review also
recomputed the classification from JUnit and passed these 41 focused tests.
The post-fix full suite completed: **296 failed, 968 passed, 11 skipped, 30 errors**,
one warning, 340.91 seconds. JUnit test-ID comparison found exactly the two corrected
harness failures resolved and no new failing or errored test IDs. The remaining
categories are unchanged: 273 explicit guard failures, five early guard failures,
15 link-permission failures, three unavailable FIFO fixtures and 30 guard setup
errors. This remains a FAIL, not full Windows support. The post-fix raw artifacts
and hashes are recorded under `postFixRun` in the JSON evidence. Independent review
covered baseline classification and focused 41/41; the full post-fix run was
inspected, not independently rerun. Five regenerated tracked evidence files were
compared, copied outside the repository and hash-verified before restoring HEAD.
Production code,
main, storage gates and user archives are unchanged. No separate production defect
has been proven by these 298 failures; this is not proof that none exists.
