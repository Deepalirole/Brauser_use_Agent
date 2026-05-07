from pathlib import Path

from dotenv import load_dotenv


def load_project_env() -> Path | None:
    """Load the nearest project .env for this package, independent of cwd."""
    package_dir = Path(__file__).resolve().parent
    candidates = (
        package_dir / ".env",
        package_dir.parent / ".env",
    )

    for env_path in candidates:
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path, override=False)
            return env_path

    return None
