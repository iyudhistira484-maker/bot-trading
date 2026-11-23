import json
from src.utils.logging import create_logger, event


def test_structured_log_is_written_to_file_and_console(tmp_path, capsys):
    logger = create_logger(str(tmp_path / 'bot.log'), 100000, 1)
    event(logger, 'heartbeat', equity_idr=100.0)
    stdout = capsys.readouterr().out
    file_text = (tmp_path / 'bot.log').read_text()
    assert json.loads(stdout)['event'] == 'heartbeat'
    assert json.loads(file_text)['equity_idr'] == 100.0
    for handler in logger.handlers:
        handler.close()
