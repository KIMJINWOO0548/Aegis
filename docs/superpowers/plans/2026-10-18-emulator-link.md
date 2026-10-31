# Emulator connection implementation

1. Build and test the independent standard-library HTTP emulator project and its documented CLI.
2. Add strict snapshot/config validation and a bounded asynchronous multi-source collector, with lifecycle tests before implementation.
3. Connect existing API read routes to collector state in explicit emulator mode; retain the fixed-fixture default and security regression tests.
4. Add a cancellable desktop client and minimal live device/signal controls, with handler/client tests and a Release build.
5. Run separate-process integration, outage/restart and documented performance measurements; review changes and report limitations.

Parallel ownership: emulator project, collector/API, desktop client/UI. Shared boundaries are fixed in the design spec; avoid overlapping edits.
