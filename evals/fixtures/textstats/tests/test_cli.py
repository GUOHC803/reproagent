import json

from textstats.cli import main


def test_cli_end_to_end(tmp_path, capsys):
    f = tmp_path / "doc.txt"
    f.write_text("The cat and the dog. The cat!")
    assert main([str(f), "--top", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tokens"] == 3 and out["unique"] == 2 and out["top"] == [["cat", 2]]
