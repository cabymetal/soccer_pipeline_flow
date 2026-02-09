from databricks.sdk.runtime import spark
from pyspark.sql import DataFrame
from pyspark.sql.functions import current_timestamp


def ingest_players_statistics(file_format: str, source_path: str) -> DataFrame:
    df = (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", file_format)
        .option("cloudFiles.inferColumnTypes", "true")
        .option("cloudFiles.schemaLocation", f"{source_path}/schemas/players/") #auto schema inference location
        .load(f"{source_path}/players/")
        .withColumn("ingestion_timestamp", current_timestamp())
    )
    return df

def load_players_statistics(df: DataFrame, schema: str, table: str) -> None:
    query = (
        df.writeStream.format("delta")
        .option("checkpointLocation", f"/tmp/checkpoints/players/")
        .outputMode("append")
        .trigger(availableNow=True)
        .table(f'{schema}.{table}')
    )
    query.awaitTermination()

def watch_players_statistics(catalog: str, schema: str, table: str) -> None:
    spark.read.table(f"{catalog}.{schema}.{table}")