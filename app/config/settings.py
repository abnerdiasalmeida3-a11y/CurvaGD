import json
import tomllib
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BDGD_", extra="forbid")
    workspace: str = ""
    batch_size: int = Field(default=32768, ge=1000, le=65536)
    memory_mb: int = Field(default=256, ge=128, le=512)
    distribuidora: str = "ENERGISA_MT"
    ano: int = Field(default=2024, ge=1900, le=2200)
    calendario: str = ""

    @classmethod
    def load(cls, path: Path):
        """Le o settings.toml; um arquivo corrompido nunca impede o programa de abrir."""
        if not path.exists():
            return cls()
        try:
            dados = tomllib.loads(path.read_text(encoding="utf-8"))
            conhecidos = {k: v for k, v in dados.items() if k in cls.model_fields}
            return cls(**conhecidos)
        except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
            import logging
            logging.getLogger("bdgd").warning("settings.toml ignorado (%s): %s", path, exc)
            return cls()

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in self.model_dump().items()) + "\n", encoding="utf-8")
        tmp.replace(path)
