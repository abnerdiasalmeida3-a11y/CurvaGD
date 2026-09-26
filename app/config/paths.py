from pathlib import Path
from PySide6.QtCore import QStandardPaths

RESOURCES = Path(__file__).parent


def user_config() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "settings.toml"


def user_workspace() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "workspace"


def prepare_workspace(path: Path):
    for part in ("datasets", "staging", "logs", "reports", "exports"):
        (path / part).mkdir(parents=True, exist_ok=True)
