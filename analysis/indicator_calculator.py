"""Deterministic V1 indicators from verified, DB-selected canonical Evidence.

Reads only the V1 evidence_records table and normalized archive sidecars.
No legacy market-table access, raw business input, network, or database writes.
"""
from datetime import date, timedelta, timezone
from decimal import Decimal, localcontext
import math

from normalizer.semantics import validate_metadata
from evidence.knowability import aware_clock, cutoff_reasons, validate_times
from persistence.read_models import EvidenceReadRepository


class IndicatorError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise IndicatorError(code)


def number(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            'CANONICAL_NUMBER_INVALID')
    return Decimal(str(value))


def _at_judgment_cutoff(content, request_timestamp):
    """Apply the same historical clocks as the package gate before calculation."""
    try:
        cutoffs = []
        for row in content:
            times = row.get('knowability')
            if times is not None:
                validate_times(times)
                fact = times['judgment_cutoff']
                if fact['value'] is not None:
                    require(fact['status'] == 'VERIFIED', 'JUDGMENT_CUTOFF_UNVERIFIED')
                    cutoffs.append(fact['value'])
        if not cutoffs:
            return content, []
        cutoff = cutoffs[0]
        require(all(aware_clock(value) == aware_clock(cutoff) for value in cutoffs),
                'JUDGMENT_CUTOFF_CONFLICT')
        retained, reasons = [], set()
        for row in content:
            times = row.get('knowability')
            if times is None:
                reasons.add('KNOWABILITY_UNVERIFIED')
                continue
            excluded = list(cutoff_reasons(times, cutoff,
                                          period_timezone=timezone(timedelta(hours=8))))
            if times['data_period']['value'] != row['trade_date']:
                excluded.append('SOURCE_TIME_CONFLICT')
            acquired = times['acquired_at']
            if acquired['status'] == 'VERIFIED' and aware_clock(request_timestamp) > aware_clock(acquired['value']):
                excluded.append('SOURCE_TIME_CONFLICT')
            if excluded:
                reasons.update(excluded)
            else:
                retained.append(row)
        if len(retained) != len(content):
            reasons.add('JUDGMENT_CUTOFF_EXCLUDED_ROWS')
        return retained, sorted(reasons)
    except (ValueError, TypeError, KeyError) as exc:
        raise IndicatorError('CANONICAL_KNOWABILITY_INVALID: ' + str(exc)) from None


class IndicatorCalculator:
    def __init__(self, db, *, archive_reader):
        self.db = db
        self.reader = archive_reader

    def calculate(self, evidence_id):
        require(type(evidence_id) is str and bool(evidence_id), 'EVIDENCE_ID_REQUIRED')
        evidence = EvidenceReadRepository(self.db, archive_reader=self.reader).evidence(evidence_id)
        require(evidence is not None and evidence['integrity_status'] == 'VERIFIED', 'EVIDENCE_NOT_VERIFIED')
        require((evidence['validation_result'] or {}).get('status') == 'PASS' and
                not evidence['validation_result'].get('failures') and evidence['human_confirm'] != 'REJECTED',
                'EVIDENCE_NOT_ACCEPTABLE')
        content_hash = evidence['hashes']['normalized_data_hash']
        require(content_hash is not None, 'CANONICAL_DATA_MISSING')
        content = self.reader.normalized(evidence['raw_data_path'], content_hash)['normalized_data']
        require(type(content) is list and 0 < len(content) <= 10000, 'DAILY_SERIES_REQUIRED')
        stocks, dates, bases, quality = set(), set(), set(), set()
        for row in content:
            require(type(row) is dict and row.get('provider') == evidence['source'] and
                    row.get('provider') in {'TUSHARE_MCP', 'TDX_OFFICIAL_LOCAL_HTTP'}, 'DAILY_SOURCE_MISMATCH')
            try:
                validate_metadata(row, 'daily_kline')
                require(date.fromisoformat(row['trade_date']).isoformat() == row['trade_date'], 'TRADE_DATE_INVALID')
            except (ValueError, KeyError, TypeError):
                raise IndicatorError('CANONICAL_SEMANTICS_INVALID') from None
            require(type(row.get('stock_id')) is str and bool(row['stock_id']), 'STOCK_ID_REQUIRED')
            require(row['trade_date'] not in dates, 'DUPLICATE_TRADE_DATE')
            dates.add(row['trade_date'])
            stocks.add(row['stock_id'])
            bases.add(row['adjustment_basis'])
            quality.add(row.get('data_quality_status'))
            prices = {k: number(row.get(k)) for k in ('open', 'high', 'low', 'close')}
            number(row.get('volume'))
            require(prices['low'] <= min(prices['open'], prices['close']) and
                    prices['high'] >= max(prices['open'], prices['close']) and
                    prices['high'] >= prices['low'], 'CANONICAL_PRICE_INVALID')
        require(len(stocks) == len(bases) == 1, 'MIXED_STOCK_OR_ADJUSTMENT')
        require(quality <= {'PASS', 'WARNING', 'UNVERIFIED'}, 'CANONICAL_QUALITY_INVALID')
        usable, reasons = _at_judgment_cutoff(content, evidence['timestamp'])
        ordered = sorted(usable, key=lambda r: r['trade_date'])
        verified_basis = bases <= {'UNADJUSTED', 'QFQ', 'HFQ'} and all(r.get('adjust_status') == 'VERIFIED' for r in ordered)
        if not verified_basis:
            reasons.append('ADJUSTMENT_UNVERIFIED')
        if quality != {'PASS'}:
            reasons.append('SOURCE_QUALITY_NOT_PASS')
        result = {'stock_id': next(iter(stocks)), 'evidence_id': evidence_id,
                  'canonical_id': evidence.get('canonical_id'), 'raw_reference_id': evidence.get('raw_reference_id'),
                  'normalized_data_hash': content_hash, 'rule_version': 'INDICATORS_V1.0',
                  'as_of': ordered[-1]['trade_date'] if ordered else None, 'sample_count': len(ordered),
                  'adjustment_basis': next(iter(bases))}
        with localcontext() as context:
            context.prec = 40
            for window in (5, 20, 60):
                available = len(ordered) >= window and verified_basis
                result['ma' + str(window)] = (float(sum(number(r['close']) for r in ordered[-window:]) / window)
                                               if available else None)
                if len(ordered) < window:
                    reasons.append('MA' + str(window) + '_INSUFFICIENT_HISTORY')
            previous = number(ordered[-2]['volume']) if len(ordered) >= 2 else None
            result['volume_change'] = (float(number(ordered[-1]['volume']) / previous - 1)
                                       if previous not in (None, 0) else None)
        if result['volume_change'] is None:
            reasons.append('VOLUME_BASE_MISSING_OR_ZERO')
        if all(result['ma' + str(n)] is not None for n in (5, 20, 60)):
            a, b, c = (result['ma' + str(n)] for n in (5, 20, 60))
            result['trend'] = 'UP' if a > b > c else 'DOWN' if a < b < c else 'SIDEWAYS'
        else:
            result['trend'] = 'UNKNOWN'
        require(all(v is None or math.isfinite(v) for k, v in result.items()
                    if k in {'ma5', 'ma20', 'ma60', 'volume_change'}), 'INDICATOR_NONFINITE')
        result.update(DATA_STATUS='PASS' if not reasons else 'PARTIAL', limitations=reasons)
        return result
