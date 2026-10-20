from pathlib import Path
import pytest
from bike_demo.followup_v4 import runner
from bike_demo.followup_v4.public_runner import isolated_output


def test_public_output_context_restores_v4_globals_after_failure(tmp_path):
    original = runner.OUT, runner.RECORD, runner.protocol
    config = {'development_folds': {'A': {'valid_start': '2025-05-15'}}}
    with pytest.raises(RuntimeError, match='deliberate'):
        with isolated_output(config, tmp_path / 'new_outputs', tmp_path / 'new_records'):
            assert runner.OUT == tmp_path / 'new_outputs'
            assert runner.RECORD == tmp_path / 'new_records'
            assert runner.protocol() is config
            raise RuntimeError('deliberate')
    assert (runner.OUT, runner.RECORD, runner.protocol) == original
