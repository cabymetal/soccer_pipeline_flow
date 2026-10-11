from soccer_bot.config import Settings
from soccer_bot.data.base import DataSource


def build_source(settings: Settings) -> DataSource:
    if settings.data_source == "databricks":
        from soccer_bot.data.databricks_source import DatabricksSource
        return DatabricksSource(settings)

    from soccer_bot.data.mock_source import MockSource
    return MockSource()
