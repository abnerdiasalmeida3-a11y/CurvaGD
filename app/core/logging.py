import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(workspace: Path):
    path = workspace / "logs" / "app.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("bdgd")
    for handler in log.handlers[:]:
        handler.close()
        log.removeHandler(handler)
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log
