"""Versioned schema resources. Paths work after a normal wheel installation."""
from pathlib import Path

def schema_path(relative):
    root = Path(__file__).resolve().parent / "schemas"
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Unknown or unsafe contract resource: " + str(relative))
    return path
