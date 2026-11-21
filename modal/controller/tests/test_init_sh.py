"""init.sh's environment filter for T3 (implementation.md §7.2, §11.3), run under dash, Debian's /bin/sh."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

INIT_SH = Path(__file__).resolve().parents[2] / "sandbox" / "init.sh"
DASH = shutil.which("dash")
pytestmark = pytest.mark.skipif(DASH is None, reason="needs dash, the /bin/sh on Debian")


def t3_environment(env: dict[str, str]) -> tuple[set[str], str]:
    """Runs keep_only as run_as does for T3. Returns the names left in the environment and the
    script's own T3_USER afterwards."""
    src = INIT_SH.read_text()
    keeps = re.search(r"^T3_KEEPS=.*$", src, re.M).group(0)
    keep_only = re.search(r"^keep_only\(\) \{\n.*?^\}$", src, re.M | re.S).group(0)
    script = f'{keeps}\n{keep_only}\nT3_USER=t3\nkeep_only PATH $T3_KEEPS\nprintf "%s\\0" "user=${{T3_USER-unset}}"\nenv -0\n'
    out = subprocess.run([DASH, "-c", script], env=env, capture_output=True, check=True).stdout.decode()
    user, *entries = [e for e in out.split("\0") if e]
    return {e.split("=", 1)[0] for e in entries}, user.removeprefix("user=")


def test_t3_keeps_only_path_and_the_claude_token():
    names, _ = t3_environment({
        "PATH": "/usr/bin:/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-x",
        "ANTHROPIC_API_KEY": "sk-ant-api03-x",
        "T3MODAL_MACHINE_SECRET": "s",
        "MODAL_SANDBOX_ID": "sb-1",
        "HOME": "/root",
    })

    assert names == {"PATH", "CLAUDE_CODE_OAUTH_TOKEN"}


def test_a_value_with_a_line_that_looks_like_a_variable_unsets_nothing_else():
    names, user = t3_environment({"PATH": "/usr/bin:/bin", "NOTE": "line one\nT3_USER=x\nT3_KEEPS=y"})

    assert (names, user) == ({"PATH"}, "t3")
