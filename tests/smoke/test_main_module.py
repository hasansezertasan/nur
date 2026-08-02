import subprocess
import sys

import nur


def test_python_m_nur_reports_version() -> None:
    # `python -m nur` must work as an alias for the console script; it runs the
    # package's __main__.py in a real subprocess (the only faithful check that
    # the module entry point is wired up and exits cleanly).
    result = subprocess.run(
        [sys.executable, "-m", "nur", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert nur.__version__ in result.stdout
