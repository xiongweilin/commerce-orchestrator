"""Project maintenance and evaluation tools."""



from pathlib import Path as _Path

_PROJECT_ROOT = _Path(__file__).resolve().parents[1]

def safe_path(user_path, must_exist=False):
    resolved = (_PROJECT_ROOT / str(user_path)).resolve()
    try:
        resolved.relative_to(_PROJECT_ROOT)
    except ValueError:
        raise ValueError(f"Path {user_path} is outside project root {_PROJECT_ROOT}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path does not exist: {resolved}")
    return resolved
