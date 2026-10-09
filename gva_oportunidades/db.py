"""Funciones básicas de SQLite. No se conecta a servicios externos."""
from pathlib import Path
import sqlite3

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "oportunidades_gva.sqlite3"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


def connect_database(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Abre una conexión con claves foráneas y filas accesibles por nombre."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str | Path = DEFAULT_DB) -> Path:
    """Crea las tablas del esquema si no existen y devuelve la ruta utilizada."""
    if not SCHEMA_PATH.is_file():
        raise FileNotFoundError(f"No se encuentra el esquema: {SCHEMA_PATH}")
    target = Path(path)
    with connect_database(target) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return target
