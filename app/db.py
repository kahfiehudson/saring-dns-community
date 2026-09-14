from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns():
    """Lightweight migration: add any model columns missing from an existing
    SQLite table (SQLAlchemy's create_all only creates missing tables, not
    missing columns on tables that already exist) - runs against every
    mapped model's table, not just one, so a new column on any of them
    (UnboundSettings originally, User's last_login_* since) gets picked up
    the same way without needing a bespoke migration each time."""
    from sqlalchemy import inspect, text

    from . import models  # noqa: F401  (ensure every model is registered on Base first)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for mapper in Base.registry.mappers:
        table = mapper.class_.__table__
        if table.name not in existing_tables:
            continue
        existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
        with engine.begin() as conn:
            for col in table.columns:
                if col.name in existing_columns:
                    continue
                py_type = col.type.python_type
                col_type = (
                    "BOOLEAN" if py_type is bool
                    else "INTEGER" if py_type is int
                    else "REAL" if py_type is float
                    else "TEXT"
                )

                # Use the column's own declared default (e.g. update_schedule's
                # "*-*-* 04:00:00") rather than a blanket 0/'' - a hardcoded blank
                # silently produced an empty, non-matching value on migration.
                # A nullable column with no declared default (e.g. User's
                # last_login_at) backfills as NULL instead, which is the
                # correct "never happened yet" value - not 0 or ''.
                default_value = col.default.arg if col.default is not None else None
                if callable(default_value):
                    default_value = None  # e.g. a datetime factory - not a static SQL literal
                if default_value is None:
                    if col.nullable:
                        default_sql = "NULL"
                    else:
                        default_sql = "0" if py_type in (bool, int, float) else "''"
                elif py_type is bool:
                    default_sql = "1" if default_value else "0"
                elif py_type in (int, float):
                    default_sql = str(default_value)
                else:
                    default_sql = "'{}'".format(str(default_value).replace("'", "''"))

                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type} DEFAULT {default_sql}"))


def _seed_initial_admin():
    """One-time migration: the dashboard used to have exactly one hardcoded
    login (TP_ADMIN_USER/TP_ADMIN_PASSWORD_HASH in .env, set up by
    install.sh). The first time this runs after upgrading to the Users
    table, copy that single account in as-is (same username, same bcrypt
    hash - not re-hashed) so the existing admin's password keeps working
    unchanged. Only fires while the table is still empty, so it's a no-op
    on every later startup and never overwrites accounts added since."""
    from . import models

    db = SessionLocal()
    try:
        if db.query(models.User).first() is not None:
            return
        if config.ADMIN_USER and config.ADMIN_PASSWORD_HASH:
            # Same normalization as the login check and the Users page's add
            # form (strip + lowercase), so this seeded account can never end
            # up in a different case than what login/add-user will look for.
            username = config.ADMIN_USER.strip().lower()
            db.add(models.User(username=username, password_hash=config.ADMIN_PASSWORD_HASH))
            db.commit()
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401  (register models on Base)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

    db = SessionLocal()
    try:
        if db.query(models.UnboundSettings).first() is None:
            db.add(models.UnboundSettings())
            db.commit()
    finally:
        db.close()

    _seed_initial_admin()
