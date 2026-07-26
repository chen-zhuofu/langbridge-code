from langbridge_code.util.session import last_turn_id
from langbridge_code.util.session_traces import append_raw_round, last_traces_turn_id


def test_last_turn_id_reads_traces_headers(tmp_path):
    run_log = tmp_path / "session-demo"
    run_log.mkdir()
    append_raw_round(run_log, 7, [{"role": "user", "content": "a"}])
    append_raw_round(run_log, 18, [{"role": "user", "content": "c"}])
    assert last_traces_turn_id(run_log) == 18
    assert last_turn_id(run_log) == 18


def test_last_turn_id_empty_session(tmp_path):
    run_log = tmp_path / "session-empty"
    run_log.mkdir()
    assert last_traces_turn_id(run_log) == 0
    assert last_turn_id(run_log) == 0