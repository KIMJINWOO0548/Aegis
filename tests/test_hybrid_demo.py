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
