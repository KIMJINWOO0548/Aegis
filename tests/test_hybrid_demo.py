"""Configuration checks run without opening sockets or launching applications."""

import pytest


def test_hybrid_demo_rejects_unbounded_runs_and_invalid_source_sizes():
    from scripts.run_hybrid_demo import parse_args
    for arguments in (["--count", "33"], ["--seconds", "0"], ["--seconds", "3601"],
                      ["--signal-count", "513"], ["--interval-ms", "0"], ["--port", "0"]):
        with pytest.raises(SystemExit):
            parse_args(arguments)


def test_hybrid_demo_defaults_keep_high_density_raw_and_independent_modules():
    from scripts.run_hybrid_demo import parse_args
    result = parse_args(["--headless", "--seconds", "5"])
    assert (result.count, result.signal_count, result.interval_ms) == (2, 256, 50)
    assert result.scenario == "NOISY-FLOW"
    assert result.headless is True


def test_hybrid_demo_enables_tray_overlay_and_automatic_port_explicitly(tmp_path):
    from scripts.run_hybrid_demo import parse_args
    result = parse_args(["--tray", "--overlay", "--auto-port", "--desktop-state-directory", str(tmp_path)])
    assert result.tray and result.overlay and result.auto_port
    assert result.desktop_state_directory == tmp_path
    for arguments in (["--headless", "--overlay"], ["--auto-port", "--port", "8780"]):
        with pytest.raises(SystemExit):
            parse_args(arguments)


def test_producer_recovery_retries_only_dead_module_with_a_bounded_budget():
    from types import SimpleNamespace
    from scripts.run_hybrid_demo import SyntheticProducerRecovery

    first = SimpleNamespace(poll=lambda: 1)
    peer = SimpleNamespace(poll=lambda: None)
    stack = SimpleNamespace(device_ids=("target", "peer"), emulator_processes=[first, peer])
    attempts, messages = [], []

    def start(index):
        attempts.append(index)
        if len(attempts) == 1:
            raise OSError("Synthetic start failed")
        stack.emulator_processes[index] = SimpleNamespace(poll=lambda: None)

    stack.start_emulator = start
    recovery = SyntheticProducerRecovery(stack, messages.append)
    for now in (0, .9, 1, 2, 3):
        recovery.check(now)
    assert attempts == [0, 0]
    assert stack.emulator_processes[1] is peer
    assert stack.emulator_processes[0].poll() is None

    stack.emulator_processes[0] = first
    for now in (4, 7, 8):
        recovery.check(now)
    assert attempts == [0, 0, 0]
    stack.emulator_processes[0] = first
    for now in (9, 20, 60):
        recovery.check(now)
    assert attempts == [0, 0, 0]
    assert sum("RESTART_EXHAUSTED" in message for message in messages) == 1
    assert all("peer" not in message for message in messages)
