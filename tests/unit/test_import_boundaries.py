"""The core never imports optional extras or other honeworks packages."""

import subprocess
import sys

EXTRAS = (
    "typer",
    "rich",
    "duckdb",
    "pyarrow",
    "openai",
    "sklearn",
    "hone_models",
    "hone_flow",
    "hone_select",
)


def test_core_imports_without_extras() -> None:
    code = (
        "import sys, importlib.abc\n"
        f"blocked = {EXTRAS!r}\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in blocked: raise ImportError('blocked ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import hone_lens, hone_lens.testing\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
