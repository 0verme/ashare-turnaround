from __future__ import annotations

from ashare_turnaround import __main__


def test_sync_sample_dry_run_has_no_provider_or_files(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASHARE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    def provider_must_not_be_constructed(*_: object, **__: object) -> None:
        raise AssertionError("dry-run constructed a provider")

    monkeypatch.setattr(__main__, "TushareProvider", provider_must_not_be_constructed)

    result = __main__.main(
        [
            "sync-sample",
            "--dry-run",
            "--codes",
            "600000.SH",
            "000001.SZ",
            "300001.SZ",
        ]
    )

    output = capsys.readouterr()
    assert result == 0
    assert "sync-sample dry-run" in output.out
    assert "remote_requests=false" in output.out
    assert "parquet_writes=false" in output.out
    assert "state_changes=false" in output.out
    assert not (tmp_path / "data").exists()


def test_sync_sample_dry_run_rejects_nonpositive_page_bound(tmp_path, capsys) -> None:
    result = __main__.main(
        [
            "sync-sample",
            "--dry-run",
            "--codes",
            "600000.SH",
            "000001.SZ",
            "300001.SZ",
            "--max-pages",
            "0",
        ]
    )

    output = capsys.readouterr()
    assert result == 2
    assert "must be positive" in output.err
    assert not (tmp_path / "data").exists()
