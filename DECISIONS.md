# DECISIONS.md

## 1. Indexes

I added a unique index on `employees.emp_code` and a unique compound index on `attendance_logs.emp_code` and `date`. These enforce uniqueness at the database level, including when concurrent requests arrive. I also added indexes for date-based attendance queries, department and joining-date filters, and late-attendance queries to support the main access patterns.

## 2. Simultaneous punch-ins

Concurrent requests may initially find no attendance record, but the unique compound index allows only one insert for a given employee and date. The successful request returns `201 Created`, while the other request catches `DuplicateKeyError` and returns `409 Conflict`. This makes the database constraint, rather than a separate existence check, the final safeguard against duplicate records.

## 3. Leaderboard ties

The leaderboard uses competition ranking: employees with equal total late minutes receive the same rank, and the next rank is skipped. Ranking is calculated before the result limit is applied. The implementation also includes employees tied at the cutoff, avoiding arbitrary exclusion of employees with the same total late minutes.

## 4. Employees without attendance logs

The department summary starts from eligible employees and joins their attendance records. Employees who joined on or before the end of the selected month remain in the headcount even if they have no attendance logs. This prevents headcount from being understated and keeps the summary anchored to the employee population rather than only to employees who have attendance records.

## 5. Scaling to 100 times the data

I tested equivalent MongoDB queries against a separate performance-test database containing 100,000 attendance records. The tested query plans used indexes, including the compound employee/date index and the date/late-attendance index. These tests demonstrate index usage for the tested query patterns, but they were not full end-to-end API benchmarks against the 100,000-record dataset.

For further scaling, I would measure the actual aggregation pipelines against representative workloads, review index selectivity and slow queries, reduce unnecessary intermediate fields, and consider pagination or caching where appropriate. Additional indexes would be based on measured query patterns because they also increase storage requirements and write costs.