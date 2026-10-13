# DECISIONS.md

## 1. Indexes

I added a unique index on `employees.emp_code` and a unique compound index on `attendance_logs.emp_code` and `date`. These enforce uniqueness at the database level, including when requests arrive concurrently. I also added indexes for date-based attendance queries, department and joining-date filters, and late-attendance queries.

## 2. Simultaneous punch-ins

Both requests may initially find no attendance record, but the unique compound index allows only one insert to succeed. The successful request returns `201 Created`; the other catches `DuplicateKeyError` and returns `409 Conflict`. This avoids relying on a separate existence check for concurrency safety.

## 3. Leaderboard ties

The leaderboard uses competition ranking. Employees with equal total late minutes receive the same rank, and the next rank is skipped. The limit is applied after ranking, so employees tied at the cutoff are included rather than arbitrarily excluded.

## 4. Employees without attendance logs

The department summary starts from eligible employees and joins their attendance records. Employees who joined on or before the end of the selected month remain in the headcount even if they have no attendance logs. This prevents headcount from being understated.

## 5. Scaling to 100 times the data

I would review query plans against a realistically sized dataset, check index selectivity, and measure aggregation performance. I would also consider reducing unnecessary fields in intermediate pipeline stages, monitoring slow queries, and using pagination or caching where appropriate. Any additional indexes would be based on measured query patterns because indexes also increase storage and write costs.
