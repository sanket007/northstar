from northstar.proc import run, CommandResult


def test_run_captures_stdout_and_returncode():
    res = run(["python3", "-c", "print('hello')"])
    assert isinstance(res, CommandResult)
    assert res.returncode == 0
    assert res.ok is True
    assert "hello" in res.stdout


def test_run_reports_nonzero_and_not_ok():
    res = run(["python3", "-c", "import sys; sys.exit(3)"])
    assert res.returncode == 3
    assert res.ok is False


def test_run_shell_string():
    res = run("echo shelltest", shell=True)
    assert "shelltest" in res.stdout
