import argparse
from databricks.sdk.runtime import spark
from soccer_api_consumer import player_stats


def main():
    # Process command-line arguments
    parser = argparse.ArgumentParser(
        description="Databricks job with catalog and schema parameters",
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--source_path", required=True)
    args = parser.parse_args()

    # Set the default catalog and schema
    spark.sql(f"USE CATALOG {args.catalog}")
    spark.sql(f"USE SCHEMA {args.schema}")

    player_stats_df = player_stats.ingest_players_statistics(file_format="parquet", source_path=args.source_path)
    player_stats.load_players_statistics(df=player_stats_df, schema=args.schema, table="player_stats")
    player_stats.watch_players_statistics(catalog=args.catalog, schema=args.schema, table="player_stats")



if __name__ == "__main__":
    main()
