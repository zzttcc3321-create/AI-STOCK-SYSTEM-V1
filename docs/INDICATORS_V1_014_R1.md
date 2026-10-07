# V1 indicator_calculator — #014-R1

`IndicatorCalculator(db, archive_reader=reader).calculate(evidence_id)` selects the V1.1 Evidence row, verifies its raw archive and canonical identity, and reads the hash-bound normalized daily series. Only `evidence_records` is queried; no legacy market table, migration, or schema path changes. Calculations do not write the database or call providers.

MA5/20/60 use the latest 5/20/60 available canonical daily observations sorted by trade_date. Volume change is latest volume / previous volume - 1 (0.15 means +15%). UP requires MA5 > MA20 > MA60; DOWN requires the reverse; otherwise SIDEWAYS. These are deterministic market descriptors, not investment recommendations. JSON retains evidence_id, canonical/raw identity, normalized hash, rule version, as_of, count and adjustment basis.

Insufficient history keeps unavailable MAs NULL and trend UNKNOWN. A missing/zero volume denominator stays NULL. Unknown adjustment suppresses price MAs/trend; source WARNING/UNVERIFIED remains PARTIAL. Mixed stocks/bases, duplicate dates, nonfinite/negative numbers, invalid OHLC or semantics, rejected Evidence and archive corruption fail closed. No fill/refetch/calendar inference occurs; these windows count available observations, and trading-calendar completeness is not certified.
