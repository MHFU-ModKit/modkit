import pytest


@pytest.fixture
def run(pytester, monkeypatch):
    def run(fixture, value):
        monkeypatch.delenv("MHFU_DATA", raising=False)
        monkeypatch.delenv("MHP3RD_DATA", raising=False)
        if value is not None:
            monkeypatch.setenv(fixture.upper(), str(value))
        pytester.makepyfile(f"def test_it({fixture}):\n    assert {fixture}.is_dir()\n")
        return pytester.runpytest()

    return run


@pytest.mark.parametrize("fixture", ["mhfu_data", "mhp3rd_data"])
def test_skips_when_unset(run, fixture):
    run(fixture, None).assert_outcomes(skipped=1)


@pytest.mark.parametrize("fixture", ["mhfu_data", "mhp3rd_data"])
def test_resolves_when_set(run, fixture, tmp_path):
    run(fixture, tmp_path).assert_outcomes(passed=1)


@pytest.mark.parametrize("fixture", ["mhfu_data", "mhp3rd_data"])
def test_errors_when_not_a_directory(run, fixture, tmp_path):
    run(fixture, tmp_path / "missing").assert_outcomes(errors=1)
