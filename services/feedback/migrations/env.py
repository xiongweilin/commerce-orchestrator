from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from feedback_app import models  # noqa: F401
from feedback_app.config import get_settings
from feedback_app.database import Base

# 这是 Alembic Config object，用于提供
# 当前 .ini 文件中的配置值。
config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

# 解析配置文件以设置 Python logging。
# 这一行用于初始化 logger。
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 在这里添加 model 的 MetaData object
# 以支持 'autogenerate'
# 示例：from myapp import mymodel
# 示例：target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# env.py 所需的其他配置值
# 可以这样读取：
# 示例：my_important_option = config.get_main_option("my_important_option")
# ……等。


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
