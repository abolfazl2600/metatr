"""Exact-decimal, explicitly assumed futures simulation economics; no trading API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, localcontext

from metatr.calendars import stamp
from metatr.exchanges.coinbase_futures import validate_contract_metadata
from metatr.futures_core import Market
from metatr.research import digest


def decimal(value) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("Money and quantity must use decimal strings, never floats")
    try:
        result = Decimal(value)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError("Invalid exact decimal") from exc
    if not result.is_finite():
        raise ValueError("Finite decimal required")
    return result


def number(value: Decimal) -> str:
    return format(value, "f")


def native_contract_fingerprint(metadata: dict) -> str:
    """Versioned structural pin; prices/funding/session state remain raw provenance.

    Existing full-response SHA pins stay valid. New operational profiles use this
    scope so public quotes changing cannot masquerade as contract revisions.
    """
    kind = validate_contract_metadata(metadata)
    details = metadata["future_product_details"]
    identity = {k: metadata.get(k) for k in (
        "product_id", "product_type", "product_venue", "base_currency_id",
        "quote_currency_id", "base_display_symbol", "quote_display_symbol",
        "price_increment", "base_increment", "base_min_size", "base_max_size",
        "quote_increment", "quote_min_size", "quote_max_size",
    )}
    identity["details"] = {k: details.get(k) for k in (
        "venue", "contract_code", "contract_size", "contract_root_unit",
    )}
    if kind == "DATED":
        identity["details"]["contract_expiry"] = details.get("contract_expiry")
    return digest({"scope": "NATIVE_CONTRACT_V1", "kind": kind, "identity": identity})


@dataclass(frozen=True)
class ContractSpec:
    market: Market
    version: str
    family: str
    kind: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    multiplier: str  # base currency units per contract
    min_quantity: str  # contracts
    quantity_step: str
    tick_size: str  # quote/base
    price_precision: int
    calendar_id: str
    calendar_version: str
    margin_currency: str
    effective_at_utc: str
    source: str
    metadata_sha256: str
    expiry_utc: str | None = None
    roll_policy: str = "HALT"
    margin_model: str = "UNSUPPORTED_SCENARIO_ONLY"

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def validate(self, metadata: dict, at_utc) -> None:
        kind = validate_contract_metadata(metadata)
        at, effective = stamp(at_utc), stamp(self.effective_at_utc)
        if (self.market.exchange != "coinbase" or self.market.asset != "BTC"
                or self.market.instrument != "FUTURE"
                or metadata["product_id"] != self.market.product_id
                or self.family != self.market.product_id.split("-")[0]
                or self.kind != kind or self.metadata_sha256 not in (
                    digest(metadata), native_contract_fingerprint(metadata))
                or self.roll_policy != "HALT" or at < effective
                or not all((self.version, self.source, self.calendar_id, self.calendar_version))):
            raise ValueError("Unverified/mismatched/ineffective contract specification")
        if (self.base_currency != "BTC" or self.quote_currency != "USD"
                or self.settlement_currency != "USD" or self.margin_currency != "USD"):
            raise ValueError("Unsupported currency conversion/settlement")
        for value in (self.multiplier, self.min_quantity, self.quantity_step, self.tick_size):
            if decimal(value) <= 0:
                raise ValueError("Contract sizes and steps must be positive")
        if (type(self.price_precision) is not int or not 0 <= self.price_precision <= 12
                or decimal(self.tick_size).as_tuple().exponent < -self.price_precision
                or decimal(self.min_quantity) % decimal(self.quantity_step)):
            raise ValueError("Invalid precision or minimum quantity step")
        details = metadata["future_product_details"]
        if decimal(details.get("contract_size")) != decimal(self.multiplier):
            raise ValueError("Contract multiplier lacks matching native metadata")
        # Optional native tick/step metadata must agree; absent values require sourced specs.
        for key, value in (("price_increment", self.tick_size),
                           ("base_increment", self.quantity_step)):
            if metadata.get(key) is not None and decimal(metadata[key]) != decimal(value):
                raise ValueError("Native increment disagrees with contract specification")
        if kind == "DATED":
            if (self.expiry_utc is None
                    or stamp(self.expiry_utc) != stamp(details["contract_expiry"])
                    or at >= stamp(self.expiry_utc)):
                raise ValueError("Expired/unknown dated contract; rollover unsupported")
        elif self.expiry_utc is not None:
            raise ValueError("Never interpret BIP pseudo-expiry as dated settlement")
        if self.margin_model != "UNSUPPORTED_SCENARIO_ONLY":
            raise ValueError("Unverified margin/liquidation model")


@dataclass(frozen=True)
class CostScenario:
    scenario_id: str
    version: str
    effective_at_utc: str
    source: str
    assumptions: str
    fee_currency: str
    maker_fee_bps: str
    taker_fee_bps: str
    spread_bps: str
    slippage_ticks: str
    participation_cap: str
    volume_units: str  # explicit public-candle volume interpretation
    funding_model: str  # conservative absolute-notional charge each bar, not actual funding
    funding_bps_per_bar: str
    latency_bars: int = 0

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def validate(self, spec: ContractSpec, at_utc) -> None:
        if (not all((self.scenario_id, self.version, self.source, self.assumptions))
                or stamp(at_utc) < stamp(self.effective_at_utc)
                or self.fee_currency != spec.settlement_currency
                or self.volume_units not in ("base", "contracts")
                or self.funding_model != "ASSUMED_ABSOLUTE_NOTIONAL_PER_BAR"
                or type(self.latency_bars) is not int or self.latency_bars != 0):
            raise ValueError("Unsupported/missing cost, funding, currency or latency scenario")
        for value in (self.maker_fee_bps, self.taker_fee_bps, self.spread_bps,
                      self.slippage_ticks, self.funding_bps_per_bar):
            if decimal(value) < 0:
                raise ValueError("Costs cannot be negative")
        if not 0 < decimal(self.participation_cap) <= 1:
            raise ValueError("Participation cap must be in (0,1]")


@dataclass(frozen=True)
class SimulatedOrder:
    market: Market
    side: str
    quantity: str
    decision_open_utc: str
    earliest_fill_utc: str
    order_type: str = "MARKET"


@dataclass(frozen=True)
class SimulatedFill:
    market: str
    timestamp: str
    side: str
    quantity: str
    price: str
    notional: str
    fee: str
    fee_currency: str
    contract_sha256: str
    cost_sha256: str
    simulated: bool = True


def estimate_fill(order: SimulatedOrder, next_bar: dict, spec: ContractSpec,
                  costs: CostScenario, metadata: dict) -> SimulatedFill:
    """All-or-none at next eligible open plus adverse costs, bounded by bar OHLC.

    This deterministic OHLC scenario is not an executable quote or a liquidity claim.
    """
    at = stamp(next_bar["timestamp"])
    spec.validate(metadata, at)
    costs.validate(spec, at)
    if (order.market != spec.market or order.side not in ("BUY", "SELL")
            or order.order_type != "MARKET" or at < stamp(order.earliest_fill_utc)
            or at <= stamp(order.decision_open_utc)):
        raise ValueError("Invalid order/market or same-bar simulation")
    with localcontext() as ctx:
        ctx.prec = 40
        quantity, step = decimal(order.quantity), decimal(spec.quantity_step)
        if quantity < decimal(spec.min_quantity) or quantity % step:
            raise ValueError("Invalid contract quantity/step")
        opened, high, low, close, volume = (decimal(next_bar[c]) for c in
                                          ("open", "high", "low", "close", "volume"))
        if (min(opened, high, low, close) <= 0 or volume < 0 or high < max(opened, close, low)
                or low > min(opened, close, high)):
            raise ValueError("Invalid next-bar OHLCV")
        base = quantity * decimal(spec.multiplier)
        needed = base if costs.volume_units == "base" else quantity
        if needed > volume * decimal(costs.participation_cap):
            raise ValueError("Insufficient assumed liquidity; no partial/fabricated fill")
        tick = decimal(spec.tick_size)
        adverse = opened * decimal(costs.spread_bps) / 20000 + (
            tick * decimal(costs.slippage_ticks))
        buying = order.side == "BUY"
        raw = opened + adverse if buying else opened - adverse
        price = (raw / tick).to_integral_value(rounding=ROUND_CEILING if buying else ROUND_FLOOR)
        price *= tick
        if price < low or price > high:
            raise ValueError("Conservative fill outside observed bar; no fill")
        notional = base * price
        fee = notional * decimal(costs.taker_fee_bps) / 10000
        return SimulatedFill(spec.market.key, at.isoformat(), order.side, number(quantity),
                             number(price), number(notional), number(fee), costs.fee_currency,
                             spec.fingerprint, costs.fingerprint)


def value_position(quantity: str, entry: str, mark: str, spec: ContractSpec) -> dict:
    """Signed contracts; futures mark-to-market only, no spot principal cash debit."""
    with localcontext() as ctx:
        ctx.prec = 40
        quantity, entry, mark = map(decimal, (quantity, entry, mark))
        if entry <= 0 or mark <= 0:
            raise ValueError("Positive entry and mark required")
        multiplier = decimal(spec.multiplier)
        return {"notional": number(abs(quantity) * multiplier * mark),
                "unrealized_pnl": number(quantity * multiplier * (mark - entry)),
                "currency": spec.settlement_currency}
