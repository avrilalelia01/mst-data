# Requirements Document

## Introduction

The Shopee Sales Analyzer is a program that connects to a Shopee seller account, retrieves sales and product data, and provides actionable recommendations to increase sales and profit. It also generates a Power BI dashboard to visualize key performance indicators (KPIs), trends, and insights. The system targets Shopee sellers who want data-driven guidance to optimize their product listings, pricing strategies, and promotional activities.

## Glossary

- **Analyzer**: The core system responsible for processing and analyzing Shopee seller data.
- **Shopee_API**: The Shopee Open Platform API used to authenticate and retrieve seller account data.
- **Data_Collector**: The module responsible for fetching and storing raw data from Shopee_API.
- **Recommendation_Engine**: The module that processes analyzed data and generates actionable sales and profit recommendations.
- **Report_Generator**: The module responsible for producing structured reports and Power BI-compatible data exports.
- **Dashboard**: The Power BI report file (.pbix) or dataset that visualizes KPIs, trends, and recommendations.
- **Seller**: A Shopee merchant who owns the seller account being analyzed.
- **KPI**: Key Performance Indicator — a measurable metric such as revenue, conversion rate, or profit margin.
- **Product_Record**: A structured data object representing a single product listing with its associated sales metrics.
- **Sales_Period**: A user-defined date range used to scope the analysis.
- **Recommendation**: A structured, actionable suggestion with a priority level, expected impact, and supporting data.
- **Data_Store**: The local or cloud database/file system where collected and processed data is persisted.

---

## Requirements

### Requirement 1: Shopee Account Authentication

**User Story:** As a Seller, I want to authenticate my Shopee seller account, so that the Analyzer can securely access my sales data.

#### Acceptance Criteria

1. THE Shopee_API SHALL support authentication via OAuth 2.0 using Shopee Open Platform credentials (Partner ID, Partner Key, and Shop ID).
2. WHEN a Seller provides valid credentials, THE Data_Collector SHALL obtain and store an access token for subsequent API requests.
3. WHEN an access token expires, THE Data_Collector SHALL automatically refresh the token without requiring the Seller to re-authenticate.
4. IF authentication fails due to invalid credentials, THEN THE Analyzer SHALL return a descriptive error message identifying the failure reason.
5. IF the Shopee_API is unreachable during authentication, THEN THE Analyzer SHALL retry the connection up to 3 times with a 5-second interval before returning a connection error.

---

### Requirement 2: Sales Data Collection

**User Story:** As a Seller, I want the Analyzer to collect all relevant sales data from my Shopee account, so that I have a complete dataset for analysis.

#### Acceptance Criteria

1. WHEN a Seller specifies a Sales_Period, THE Data_Collector SHALL retrieve all orders within that date range from Shopee_API.
2. THE Data_Collector SHALL collect the following data per order: order ID, order status, item list, quantities, unit prices, total payment, shipping fee, discounts applied, and order creation timestamp.
3. THE Data_Collector SHALL collect Product_Records including: product ID, product name, category, current price, stock quantity, number of reviews, average rating, and total units sold.
4. WHEN data collection is complete, THE Data_Collector SHALL persist all collected data to the Data_Store.
5. IF a partial data retrieval failure occurs during collection, THEN THE Data_Collector SHALL log the failed records and continue collecting remaining data.
6. WHEN data collection completes, THE Data_Collector SHALL report the total number of records collected and the number of failed records to the Seller.

---

### Requirement 3: Sales Performance Analysis

**User Story:** As a Seller, I want the Analyzer to compute sales performance metrics, so that I can understand how my store is performing.

#### Acceptance Criteria

1. WHEN analysis is triggered, THE Analyzer SHALL compute the following KPIs for the specified Sales_Period: total revenue, total orders, total units sold, average order value, gross profit margin, and return rate.
2. THE Analyzer SHALL compute per-product metrics including: revenue contribution, units sold, profit margin per unit, and conversion rate (views to orders ratio).
3. THE Analyzer SHALL identify the top 10 best-performing products by revenue and by units sold within the Sales_Period.
4. THE Analyzer SHALL identify the bottom 10 underperforming products by revenue within the Sales_Period.
5. WHEN analysis is triggered, THE Analyzer SHALL compute month-over-month and week-over-week revenue trends for the Sales_Period.
6. IF insufficient data exists for trend computation (fewer than 2 periods), THEN THE Analyzer SHALL indicate that trend data is unavailable and display only available metrics.

---

### Requirement 4: Sales and Profit Recommendations

**User Story:** As a Seller, I want to receive actionable recommendations, so that I can increase my sales and profit based on data-driven insights.

#### Acceptance Criteria

1. WHEN analysis is complete, THE Recommendation_Engine SHALL generate recommendations across the following categories: pricing optimization, inventory management, promotional opportunities, and product listing improvements.
2. THE Recommendation_Engine SHALL assign each Recommendation a priority level of High, Medium, or Low based on the estimated revenue or profit impact.
3. THE Recommendation_Engine SHALL include supporting data for each Recommendation, identifying the specific product(s) or metric(s) that triggered the recommendation.
4. WHEN a product's profit margin falls below 10%, THE Recommendation_Engine SHALL generate a High-priority pricing or cost-reduction recommendation for that product.
5. WHEN a product's stock quantity reaches 0 and the product has recorded sales in the last 30 days, THE Recommendation_Engine SHALL generate a High-priority restocking recommendation.
6. WHEN a product has a rating below 3.5 stars and more than 10 reviews, THE Recommendation_Engine SHALL generate a Medium-priority product improvement recommendation.
7. WHEN a product's conversion rate is below 2% and it has received more than 100 views in the Sales_Period, THE Recommendation_Engine SHALL generate a Medium-priority listing optimization recommendation.
8. THE Recommendation_Engine SHALL produce a minimum of 3 and a maximum of 20 recommendations per analysis run, prioritized by estimated impact.

---

### Requirement 5: Report Generation

**User Story:** As a Seller, I want to export analysis results to a structured report, so that I can review and share insights.

#### Acceptance Criteria

1. WHEN analysis is complete, THE Report_Generator SHALL produce a summary report in JSON format containing all computed KPIs, product metrics, and recommendations.
2. THE Report_Generator SHALL produce a CSV export of all Product_Records and their computed metrics for the analyzed Sales_Period.
3. THE Report_Generator SHALL produce a Power BI-compatible data export in either CSV or a structured format consumable by Power BI Desktop (e.g., CSV files organized by dimension and fact tables).
4. WHEN the Report_Generator produces output files, THE Report_Generator SHALL name each file with the format `{shop_id}_{start_date}_{end_date}_{report_type}.{extension}`.
5. IF a report file with the same name already exists, THEN THE Report_Generator SHALL overwrite the existing file and log a warning indicating the overwrite.

---

### Requirement 6: Power BI Dashboard Generation

**User Story:** As a Seller, I want a Power BI dashboard, so that I can visually explore my sales data and KPIs interactively.

#### Acceptance Criteria

1. THE Report_Generator SHALL produce structured data exports compatible with Power BI Desktop, organized into the following tables: Sales_Fact, Product_Dim, Date_Dim, and Recommendation_Fact.
2. THE Dashboard SHALL include the following visualizations: total revenue over time (line chart), top 10 products by revenue (bar chart), profit margin distribution (histogram), order volume heatmap by day of week and hour, and recommendation list by priority.
3. WHERE a Seller has multiple Sales_Periods loaded, THE Dashboard SHALL support filtering all visualizations by Sales_Period using a date range slicer.
4. THE Dashboard data export SHALL include a Date_Dim table with pre-calculated fiscal week, month, quarter, and year columns to enable standard Power BI time-intelligence functions.
5. WHEN a Seller loads the exported data into Power BI Desktop, THE Dashboard template (.pbit file) SHALL render all visualizations without requiring manual data mapping.

---

### Requirement 7: Data Freshness and Scheduling

**User Story:** As a Seller, I want the Analyzer to support scheduled data refreshes, so that my dashboard always reflects up-to-date information.

#### Acceptance Criteria

1. THE Analyzer SHALL support configuring a data refresh schedule with intervals of: hourly, daily, or weekly.
2. WHEN a scheduled refresh is triggered, THE Data_Collector SHALL collect data for the period since the last successful collection run.
3. IF a scheduled refresh fails, THEN THE Analyzer SHALL log the failure with a timestamp and reason, and retry the refresh at the next scheduled interval.
4. THE Analyzer SHALL maintain a refresh history log recording: run timestamp, status (success or failure), records collected, and duration in seconds.
5. WHEN a scheduled refresh completes successfully, THE Analyzer SHALL update the Data_Store and regenerate all report outputs for the affected Sales_Period.

---

### Requirement 8: Configuration and Setup

**User Story:** As a Seller, I want to configure the Analyzer through a configuration file, so that I can set my credentials and preferences without modifying source code.

#### Acceptance Criteria

1. THE Analyzer SHALL read all configuration from a single configuration file in a well-defined format (e.g., JSON or YAML).
2. THE Analyzer SHALL support the following configuration parameters: Shopee Partner ID, Partner Key, Shop ID, Data_Store path, output directory, refresh schedule, and Sales_Period default range in days.
3. IF the configuration file is missing or malformed, THEN THE Analyzer SHALL return a descriptive error identifying the missing or invalid fields before performing any operations.
4. THE Configuration_Parser SHALL parse the configuration file into a typed Configuration object.
5. THE Pretty_Printer SHALL format Configuration objects back into a valid configuration file.
6. FOR ALL valid Configuration objects, parsing then printing then parsing SHALL produce an equivalent Configuration object (round-trip property).

---

### Requirement 9: Error Handling and Logging

**User Story:** As a Seller, I want the Analyzer to handle errors gracefully and maintain logs, so that I can diagnose issues without data loss.

#### Acceptance Criteria

1. THE Analyzer SHALL write structured log entries to a log file for all operations, including data collection, analysis, recommendation generation, and report export.
2. WHEN an unhandled exception occurs during any operation, THE Analyzer SHALL log the exception with a stack trace and continue remaining operations rather than terminating the entire run.
3. THE Analyzer SHALL support configurable log verbosity levels: ERROR, WARN, INFO, and DEBUG.
4. IF the Data_Store becomes unavailable during a write operation, THEN THE Analyzer SHALL retry the write up to 3 times with a 2-second interval before logging a fatal error and halting the affected operation.
5. THE Analyzer SHALL produce a run summary at the end of each execution indicating: operations completed, operations failed, total records processed, and total execution time in seconds.
