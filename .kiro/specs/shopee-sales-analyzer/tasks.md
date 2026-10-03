# Implementation Plan: Shopee Sales Analyzer

## Overview

Implement the Shopee Sales Analyzer as a Python 3.11+ application following the layered pipeline architecture defined in the design. Tasks proceed bottom-up: project scaffolding → data models → configuration → auth → data collection → KPI engine → recommendation engine → report generation → scheduling → logging/summary → CLI wiring.

## Tasks

- [x] 1. Scaffold project structure and shared utilities
  - Create directory layout: `shopee_analyzer/`, `tests/unit/`, `tests/integration/`
  - Add `pyproject.toml` (or `setup.cfg`) with dependencies: `requests`, `apscheduler`, `pydantic>=2`, `pyyaml`, `python-json-logger`, `pandas`, `pytest`, `hypothesis`, `psycopg2-binary` (optional extra)
  - Create `shopee_analyzer/__init__.py`, `shopee_analyzer/models.py`, `shopee_analyzer/exceptions.py`
  - Implement shared `retry_with_backoff` decorator in `shopee_analyzer/utils.py` supporting configurable `max_attempts`, `delay_seconds`, and `exceptions` tuple
  - _Requirements: 1.5, 4.4, 9.4_

- [x] 2. Implement data models
  - [x] 2.1 Define all dataclasses in `shopee_analyzer/models.py`
    - Implement `Configuration`, `TokenPair`, `SalesPeriod`, `Order`, `OrderItem`, `Product`, `ProductMetrics`, `StoreKPIs`, `TrendData`, `PeriodBucket`, `Recommendation`, `CollectionResult`, `AnalysisResult`, `RefreshHistoryEntry` exactly as specified in the design
    - _Requirements: 2.2, 2.3, 3.1, 3.2, 4.1, 7.4, 8.2_

- [x] 3. Implement Configuration Parser
  - [x] 3.1 Implement `ConfigurationParser` in `shopee_analyzer/config.py`
    - Read `config.yaml` or `config.json` and produce a validated `Configuration` dataclass using Pydantic v2
    - Implement `parse(path: Path) -> Configuration` — fail fast with field-level error messages when required fields are missing or malformed
    - Implement `print_config(config: Configuration, path: Path) -> None` — serialise back to YAML/JSON preserving all fields
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 3.2 Write property test for Configuration round-trip (Property 1)
    - **Property 1: Configuration round-trip**
    - **Validates: Requirements 8.4, 8.5, 8.6**
    - Use `config_strategy()` Hypothesis strategy; run `parse(print_config(config))` and assert structural equivalence
    - File: `tests/unit/test_configuration.py`

  - [x] 3.3 Write property test for Configuration error messages naming missing fields (Property 2)
    - **Property 2: Configuration error messages name missing fields**
    - **Validates: Requirements 8.3**
    - Generate configs with one or more required fields removed; assert the error message contains each missing field name
    - File: `tests/unit/test_configuration.py`

- [x] 4. Implement Auth Client
  - [x] 4.1 Implement `AuthClient` in `shopee_analyzer/auth.py`
    - Implement `get_access_token(auth_code)`, `refresh_token(refresh_token)`, `get_valid_token()`, and `sign_request(path, access_token)` using HMAC-SHA256 signing pattern from design
    - Store `TokenPair` in the Data Store; transparently refresh before expiry in `get_valid_token()`
    - Apply `@retry_with_backoff(max_attempts=3, delay_seconds=5)` on all outbound HTTP calls
    - Return descriptive error message on auth failure (invalid credentials)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 4.2 Write unit tests for AuthClient
    - Test token acquisition, refresh, and HMAC-SHA256 signature construction with mocked HTTP
    - Test retry behaviour: exactly 3 retries at 5-second intervals on connection failure
    - Test descriptive error message on invalid-credentials response
    - File: `tests/unit/test_auth_client.py`

- [x] 5. Implement Data Store
  - [x] 5.1 Implement `DataStore` in `shopee_analyzer/storage.py`
    - Create SQLite schema (or PostgreSQL via psycopg2) for orders, order items, products, tokens, and refresh history
    - Implement read/write methods for all entity types
    - Apply `@retry_with_backoff(max_attempts=3, delay_seconds=2)` on all write operations
    - Log a fatal error and halt the affected write operation after all retries are exhausted
    - _Requirements: 2.4, 7.4, 9.4_

- [x] 6. Implement Data Collector
  - [x] 6.1 Implement `DataCollector` in `shopee_analyzer/collector.py`
    - Implement `collect_orders(period)`, `collect_products()`, and `collect_incremental(since)` using cursor-based pagination against Shopee v2 endpoints
    - On partial failure, log failed record IDs and continue collecting; return accurate `CollectionResult`
    - Report total collected and failed counts at completion
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 6.2 Write property test for Order data mapping completeness (Property 3)
    - **Property 3: Order data mapping completeness**
    - **Validates: Requirements 2.2**
    - Use `api_order_response_strategy()` to generate raw Shopee order response dicts; assert all required `Order` fields are populated and non-null
    - File: `tests/unit/test_data_collector.py`

  - [x] 6.3 Write property test for Product data mapping completeness (Property 4)
    - **Property 4: Product data mapping completeness**
    - **Validates: Requirements 2.3**
    - Use `api_product_response_strategy()` to generate raw Shopee product response dicts; assert all required `Product` fields are populated and non-null
    - File: `tests/unit/test_data_collector.py`

  - [x] 6.4 Write property test for paginated collection completeness (Property 5)
    - **Property 5: Paginated collection completeness**
    - **Validates: Requirements 2.1**
    - Generate arbitrary paginated API response sequences; assert total collected count equals sum of all per-page record counts
    - File: `tests/unit/test_data_collector.py`

  - [x] 6.5 Write property test for collection count accuracy (Property 6)
    - **Property 6: Collection count accuracy**
    - **Validates: Requirements 2.6**
    - Generate mixed success/failure fetch sequences; assert `records_collected + records_failed` equals total attempted fetches
    - File: `tests/unit/test_data_collector.py`

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement KPI Engine
  - [x] 8.1 Implement `KPIEngine` in `shopee_analyzer/kpi_engine.py`
    - Implement `compute_store_kpis(orders, period)`, `compute_product_metrics(orders, products)`, `rank_products(metrics, by, n)`, and `compute_trends(orders, period)` as pure functions with no I/O
    - When fewer than 2 time periods exist for trend computation, set `mom_revenue_change` / `wow_revenue_change` to `None`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 8.2 Write property test for KPI mathematical invariants (Property 7)
    - **Property 7: KPI mathematical invariants**
    - **Validates: Requirements 3.1**
    - Use `order_strategy()` with `min_size=1`; assert `total_revenue == sum(order.total_payment)`, `average_order_value == total_revenue / total_orders`, `total_units_sold == sum of all item quantities`, `return_rate in [0.0, 1.0]`
    - File: `tests/unit/test_kpi_engine.py`

  - [x] 8.3 Write property test for per-product metric correctness (Property 8)
    - **Property 8: Per-product metric correctness**
    - **Validates: Requirements 3.2**
    - Use `order_strategy()` and `product_strategy()`; assert `revenue_contribution` equals sum of `unit_price * quantity` for matching order lines, and `conversion_rate` formula holds
    - File: `tests/unit/test_kpi_engine.py`

  - [x] 8.4 Write property test for product ranking ordering invariants (Property 9)
    - **Property 9: Product ranking ordering invariants**
    - **Validates: Requirements 3.3, 3.4**
    - Use `product_metrics_strategy()` with arbitrary N; assert result is sorted descending, length is `min(N, len(metrics))`, and every included product beats every excluded product on the criterion
    - File: `tests/unit/test_kpi_engine.py`

- [x] 9. Implement Recommendation Engine
  - [x] 9.1 Implement `RecommendationEngine` in `shopee_analyzer/recommendation_engine.py`
    - Implement `generate(kpis, product_metrics)` applying all six rule functions: `low_margin_rule`, `out_of_stock_rule`, `low_rating_rule`, `low_conversion_rule`, `pricing_opportunity_rule`, `promotional_rule`
    - Trim output to `[3, 20]` recommendations prioritised by estimated impact
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 9.2 Write property test for Recommendation structural invariants (Property 10)
    - **Property 10: Recommendation structural invariants**
    - **Validates: Requirements 4.2, 4.3**
    - Generate arbitrary product/order datasets; assert every recommendation has `priority` in `{"HIGH","MEDIUM","LOW"}`, non-empty `supporting_products` or `supporting_metrics`, and `category` in `{"pricing","inventory","promotional","listing"}`
    - File: `tests/unit/test_recommendation_engine.py`

  - [x] 9.3 Write property test for Recommendation trigger rules (Property 11)
    - **Property 11: Recommendation trigger rules**
    - **Validates: Requirements 4.4, 4.5, 4.6, 4.7**
    - For each trigger condition, generate datasets that satisfy exactly that condition; assert the correct priority recommendation is produced for the triggering product
    - File: `tests/unit/test_recommendation_engine.py`

  - [x] 9.4 Write property test for Recommendation count bounds (Property 12)
    - **Property 12: Recommendation count bounds**
    - **Validates: Requirements 4.8**
    - Generate datasets with sufficient triggers (≥3 trigger conditions met); assert `3 <= len(recommendations) <= 20`
    - File: `tests/unit/test_recommendation_engine.py`

- [x] 10. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement Report Generator
  - [x] 11.1 Implement `ReportGenerator` in `shopee_analyzer/report_generator.py`
    - Implement `write_json_summary(result, output_dir)` producing JSON with keys `kpis`, `product_metrics`, `recommendations`, `period`, `shop_id`
    - Implement `write_product_csv(metrics, output_dir)` producing one row per product with all metric columns
    - Implement `write_powerbi_export(result, output_dir)` producing four star-schema CSV files: `Sales_Fact`, `Product_Dim`, `Date_Dim`, `Recommendation_Fact`
    - Name all output files as `{shop_id}_{start_date}_{end_date}_{report_type}.{extension}` (dates as YYYY-MM-DD)
    - Log a WARNING before overwriting any existing file
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.4_

  - [x] 11.2 Write property test for JSON summary completeness (Property 13)
    - **Property 13: JSON summary completeness**
    - **Validates: Requirements 5.1**
    - Generate arbitrary `AnalysisResult` objects; assert the produced JSON file contains all required top-level keys
    - File: `tests/unit/test_report_generator.py`

  - [x] 11.3 Write property test for Product CSV completeness (Property 14)
    - **Property 14: Product CSV completeness**
    - **Validates: Requirements 5.2**
    - Generate arbitrary `list[ProductMetrics]`; assert CSV has exactly one row per product and all required column headers
    - File: `tests/unit/test_report_generator.py`

  - [x] 11.4 Write property test for output filename format (Property 15)
    - **Property 15: Output filename format**
    - **Validates: Requirements 5.4**
    - Generate arbitrary `(shop_id, start_date, end_date, report_type)` combinations; assert every generated filename matches `{shop_id}_{YYYY-MM-DD}_{YYYY-MM-DD}_{report_type}.{ext}`
    - File: `tests/unit/test_report_generator.py`

  - [x] 11.5 Write property test for Power BI export table completeness (Property 16)
    - **Property 16: Power BI export table completeness**
    - **Validates: Requirements 6.1**
    - Generate arbitrary `AnalysisResult`; assert exactly four CSV files are produced and each contains all required columns per the star-schema model
    - File: `tests/unit/test_report_generator.py`

  - [x] 11.6 Write property test for Date_Dim coverage and column completeness (Property 17)
    - **Property 17: Date_Dim coverage and column completeness**
    - **Validates: Requirements 6.4**
    - Use `date_range_strategy()` to generate arbitrary date ranges; assert `Date_Dim` has exactly one row per calendar date, all time-intelligence columns are non-null
    - File: `tests/unit/test_report_generator.py`

- [x] 12. Implement Scheduler
  - [x] 12.1 Implement `RefreshScheduler` in `shopee_analyzer/scheduler.py`
    - Wrap APScheduler `BackgroundScheduler` with `start(config, pipeline)`, `stop()`, and `get_history()` methods
    - Map `hourly`/`daily`/`weekly` config values to correct APScheduler interval/cron triggers
    - On each trigger, call `collect_incremental(since=last_successful_run_timestamp)` and persist a `RefreshHistoryEntry` (SUCCESS or FAILURE with reason)
    - On scheduler failure, log with timestamp and reason; retry at next scheduled interval
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 12.2 Write property test for incremental collection window correctness (Property 18)
    - **Property 18: Incremental collection window correctness**
    - **Validates: Requirements 7.2**
    - Generate arbitrary refresh histories with a last-success timestamp `T`; assert incremental window starts at exactly `T` (inclusive)
    - File: `tests/unit/test_scheduler.py`

  - [x] 12.3 Write property test for refresh history entry completeness (Property 19)
    - **Property 19: Refresh history entry completeness**
    - **Validates: Requirements 7.4**
    - Simulate both successful and failed refresh runs; assert every resulting `RefreshHistoryEntry` has non-null `run_id`, `run_timestamp`, `status`, `records_collected`, `duration_seconds`
    - File: `tests/unit/test_scheduler.py`

  - [x] 12.4 Write unit tests for schedule parsing
    - Assert `hourly`, `daily`, and `weekly` config values each produce the correct APScheduler trigger type and interval
    - File: `tests/unit/test_scheduler.py`

- [x] 13. Implement Logging and Run Summary
  - [x] 13.1 Implement structured logging in `shopee_analyzer/logging_setup.py`
    - Configure `python-json-logger` with configurable verbosity levels (DEBUG, INFO, WARN, ERROR) read from `Configuration.log_level`
    - Write JSON log entries for all operations (data collection, analysis, recommendation, report export) containing `timestamp`, `level`, `operation`, `shop_id`, `message`, and relevant metrics
    - Implement run summary: at end of execution, log and print `operations_completed`, `operations_failed`, `total_records_processed`, `total_execution_time_seconds`
    - _Requirements: 9.1, 9.2, 9.3, 9.5_

  - [x] 13.2 Write property test for structured log entry completeness (Property 20)
    - **Property 20: Structured log entry completeness**
    - **Validates: Requirements 9.1**
    - For each operation type, assert at least one log entry is written containing `operation`, `timestamp`, and `outcome`
    - File: `tests/unit/test_logging.py`

  - [x] 13.3 Write property test for run summary completeness (Property 21)
    - **Property 21: Run summary completeness**
    - **Validates: Requirements 9.5**
    - Generate arbitrary mixes of operation successes and failures; assert the run summary always contains non-null `operations_completed`, `operations_failed`, `total_records_processed`, `total_execution_time_seconds`
    - File: `tests/unit/test_logging.py`

- [x] 14. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Wire pipeline and implement CLI entry point
  - [x] 15.1 Implement `Pipeline` class in `shopee_analyzer/pipeline.py`
    - Orchestrate full execution flow: parse config → authenticate → collect → compute KPIs → generate recommendations → export reports → log summary
    - Catch unhandled exceptions per operation, log with stack trace, mark as failed in summary, continue remaining operations
    - After successful scheduled refresh, update Data Store and regenerate all reports for the affected Sales_Period
    - _Requirements: 7.5, 9.2, 9.5_

  - [x] 15.2 Implement CLI entry point in `shopee_analyzer/cli.py`
    - Accept `--config`, `--start-date`, `--end-date`, `--daemon` (runs scheduler), and `--init-config` (writes default config) flags
    - Read and validate configuration via `ConfigurationParser` before any operation; print descriptive errors and exit on invalid config
    - Start `RefreshScheduler` when `--daemon` is used; run single pipeline pass otherwise
    - _Requirements: 8.1, 8.2, 8.3, 7.1_

  - [x] 15.3 Write integration tests for the full pipeline
    - Tag with `@pytest.mark.integration`; skip without credentials
    - Test end-to-end OAuth 2.0 flow against Shopee sandbox
    - Test full pipeline run producing all four Power BI CSV files
    - Test scheduler triggering incremental collection and updating the Data Store
    - File: `tests/integration/test_pipeline_integration.py`

- [x] 16. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- Checkpoints at tasks 7, 10, 14, and 16 ensure incremental validation across major layers
- Property tests validate universal correctness invariants (Properties 1–21) and complement example-based unit tests
- Integration tests (task 15.3) require Shopee sandbox credentials and are skipped in standard CI
- The `partner_key` field must never be written to logs — reference by key name only

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1"] },
    { "id": 1, "tasks": ["3.1", "5.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.1"] },
    { "id": 3, "tasks": ["4.2", "6.1"] },
    { "id": 4, "tasks": ["6.2", "6.3", "6.4", "6.5", "8.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "8.4", "9.1"] },
    { "id": 6, "tasks": ["9.2", "9.3", "9.4", "11.1"] },
    { "id": 7, "tasks": ["11.2", "11.3", "11.4", "11.5", "11.6", "12.1"] },
    { "id": 8, "tasks": ["12.2", "12.3", "12.4", "13.1"] },
    { "id": 9, "tasks": ["13.2", "13.3", "15.1"] },
    { "id": 10, "tasks": ["15.2"] },
    { "id": 11, "tasks": ["15.3"] }
  ]
}
```
