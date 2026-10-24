"""Additive ledger metadata and fail-closed checks; never rebuild a damaged DB."""
import json


class BudgetStateError(ValueError):
    """The shared ledger cannot safely authorize another provider call."""


def policy_json(project, monthly, providers):
    return json.dumps(dict(project=project, monthly=monthly, providers=providers), sort_keys=True)


def initialize(db, policy):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='api_budget_policy'").fetchone()
    auxiliaries = {'api_budget_quotes','api_budget_executions','api_budget_overrun_reviews'}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if (exists and not auxiliaries<=tables) or (not exists and auxiliaries&tables):
        raise BudgetStateError('予算台帳の管理テーブルが欠損しています。自動再作成せず停止しました。')
    db.execute('CREATE TABLE IF NOT EXISTS api_budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), policy TEXT NOT NULL)')
    if not exists:
        db.execute('INSERT INTO api_budget_policy VALUES (1, ?)', (policy,))
    db.execute('''CREATE TABLE IF NOT EXISTS api_budget_quotes (
        request_id TEXT PRIMARY KEY, evidence TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS api_budget_executions (
        request_id TEXT PRIMARY KEY, claimed_at TEXT NOT NULL, claim_id TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS api_budget_overrun_reviews (
        request_id TEXT PRIMARY KEY, reason TEXT NOT NULL, reviewed_at TEXT NOT NULL)''')
    if not exists:
        # Old "reserved" did not distinguish unsent from already dispatched.
        # Keep the original rows and money; only add a conservative legacy claim.
        db.execute("""INSERT INTO api_budget_executions
            SELECT request_id, created_at, 'legacy-unverified' FROM api_budget_requests
            WHERE state='reserved'""")
    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise BudgetStateError('予算台帳が破損しています。新しいAPI処理を停止しました。')
    validate(db, policy)


def validate(db, policy):
    if db.execute('SELECT id, policy FROM api_budget_policy').fetchall() != [(1, policy)]:
        raise BudgetStateError('共有台帳の予算設定が一致しません。全プロセスで同じ上限・プロバイダー設定を使用してください。')
    bad = db.execute('''SELECT 1 FROM api_budget_requests WHERE
        typeof(quote_units)!='integer' OR quote_units<=0 OR quote_units>9000000000000000
        OR state NOT IN ('reserved','uncertain','settled','cancelled')
        OR (state='settled' AND (actual_units IS NULL OR typeof(actual_units)!='integer'
                                OR actual_units<0 OR actual_units>9000000000000000))
        OR (state!='settled' AND actual_units IS NOT NULL)
        OR length(request_id)=0 OR length(project_id)=0
        OR month_key NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'
        OR substr(month_key,6,2) NOT BETWEEN '01' AND '12'
        OR length(provider)=0 OR length(model)=0 OR length(kind)=0 LIMIT 1''').fetchone()
    for name in ('api_budget_quotes', 'api_budget_executions', 'api_budget_overrun_reviews'):
        bad = bad or db.execute(f'''SELECT 1 FROM {name} a LEFT JOIN api_budget_requests r
            ON a.request_id=r.request_id WHERE r.request_id IS NULL LIMIT 1''').fetchone()
    bad = bad or db.execute('''SELECT 1 FROM api_budget_executions e JOIN api_budget_requests r
        ON e.request_id=r.request_id WHERE r.state='cancelled' LIMIT 1''').fetchone()
    if bad:
        raise BudgetStateError('予算台帳の状態が不整合です。作り直さずバックアップと請求明細を確認してください。')
    for raw, units, provider, model in db.execute('''SELECT q.evidence, r.quote_units, r.provider, r.model
        FROM api_budget_quotes q JOIN api_budget_requests r ON q.request_id=r.request_id'''):
        try:
            validate_quote(json.loads(raw),units,provider,model)
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as error:
            raise BudgetStateError('料金見積の根拠と予約額が不整合です。API実行を停止しました。') from error


def validate_quote(evidence, units, provider, model, *, current=False, now=None):
    from .pricing import PriceQuote, PriceRates
    from .ledger import _yen_to_units
    rates = PriceRates(**evidence['rates'])
    quote = PriceQuote(rates,fx_jpy_per_usd=evidence['fx_jpy_per_usd'],
        safety_multiplier=evidence['safety_multiplier'],tax_multiplier=evidence['tax_multiplier'])
    computed = quote.estimate_jpy(**evidence['bounds'])
    if (rates.provider!=provider or rates.model!=model or type(evidence['max_attempts']) is not int
        or evidence['max_attempts']!=1 or _yen_to_units(evidence['computed_max_jpy'],positive=True)!=_yen_to_units(computed,positive=True)
        or _yen_to_units(evidence['reserved_max_jpy'],positive=True)!=units or _yen_to_units(computed)>units):
        raise ValueError('見積と対象が一致しません。')
    if current:
        quote.execution_evidence(provider,model,{**evidence['bounds'],'retry_limit':0},now=now)


def blockers(db):
    uncertain = db.execute("""SELECT count(*) FROM api_budget_requests r
        LEFT JOIN api_budget_executions e ON r.request_id=e.request_id
        WHERE r.state='uncertain' OR (r.state='reserved' AND e.claim_id='legacy-unverified')""").fetchone()[0]
    overrun = db.execute('''SELECT count(*) FROM api_budget_requests r
        LEFT JOIN api_budget_overrun_reviews a ON r.request_id=a.request_id
        WHERE r.state='settled' AND r.actual_units>r.quote_units AND a.request_id IS NULL''').fetchone()[0]
    return uncertain, overrun


def monthly_where(provider=False):
    # A pending bill can still arrive this month, regardless of its reservation month.
    return "(month_key=? OR state IN ('reserved','uncertain'))" + (" AND provider=?" if provider else '')
