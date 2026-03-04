"""
vacant_electric - Vacant Utility Billback Pipeline

Public API:
    run_ve()     - Convenience function for running the full pipeline
    VEPipeline   - Orchestrator class (for step-by-step control)
    VEConfig     - Pipeline configuration
    VEResult     - Pipeline output container
    VEStats      - Aggregate statistics
"""
from .config import VEConfig
from .models import VEResult, VEStats
from .pipeline import VEPipeline


def run_ve(month, year, snowflake_conn, admin_fees=None, corrections_csv_path=None, output_dir=None):
    """
    Convenience function for running the full VE pipeline.

    Args:
        month: Billing month (1-12)
        year: Billing year (e.g. 2026)
        snowflake_conn: Active Snowflake connection
        admin_fees: Dict of entityid -> admin fee amount (optional)
        corrections_csv_path: Path to ALL_AI_CORRECTIONS.csv (optional)
        output_dir: Directory for output files (optional)

    Returns:
        VEResult with all pipeline outputs.
    """
    config = VEConfig(
        month=month,
        year=year,
        admin_fees=admin_fees or {},
        corrections_csv_path=corrections_csv_path,
        output_dir=output_dir,
    )
    pipeline = VEPipeline(config)
    return pipeline.run(snowflake_conn)


__all__ = ['run_ve', 'VEPipeline', 'VEConfig', 'VEResult', 'VEStats']
