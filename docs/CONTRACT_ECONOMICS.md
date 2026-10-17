# Futures simulation economics

`ContractSpec` pins exact native product identity, BIP/BIT family/kind, version,
source/effective UTC, public metadata digest, BTC-per-contract multiplier,
contract quantity minimum/step, USD/BTC tick/precision, session calendar version,
currencies and expiry policy. Specifications are supplied explicitly; no default
live fee, margin or calendar is invented. Native contract size must exist and
match; native increments, when supplied, must agree. Missing critical specs block
simulation. BIP pseudo-expiry is never treated as BIT maturity. BIT needs an exact
native expiry and halts at expiry; automatic rollover is unsupported.

`CostScenario` pins version/source/effective UTC and written assumptions, separate
maker/taker bps, full spread bps (half applied per side), adverse slippage ticks,
volume interpretation, participation cap and financing scenario. Market simulation
uses taker fees. Zero assumed funding must still be explicitly labeled with
`ASSUMED_ABSOLUTE_NOTIONAL_PER_BAR`; it is not a claim about actual Coinbase funding.
Actual margin, funding schedules and liquidation rules are unsupported. Every
spec currently declares `UNSUPPORTED_SCENARIO_ONLY` margin economics. These outputs
are suitable for hypothetical research, not eligibility for leveraged real orders.

All money, price and quantity inputs use decimal strings; floating ledger values,
NaN/Inf, negative costs, inconsistent currency conversion and invalid steps reject.
Calculation uses a local 40-digit Decimal context. No implicit currency rounding is
performed: exact decimals are preserved; a venue settlement rounding rule would
need a separately verified versioned specification.

`estimate_fill(order,next_bar,spec,costs,metadata)` supports all-or-none **market
simulation only**, with next-bar open plus adverse spread/slippage, BUY rounded
up and SELL down to native tick. A same-bar or early fill is refused. Insufficient
participation volume or a simulated price outside next-bar OHLC rejects the fill.
Volume is explicitly labeled `base` or `contracts`; no microstructure inference is
made. OHLC participation is an ex-post bar scenario, not an order-book quote or
proof that the fill was available at the open. A runner must wait until that bar
is closed before finalizing its simulated fill. Added latency is unsupported.

Worked synthetic fixture: 2 contracts × 0.01 BTC at BUY 10,001 USD/BTC yields
200.02 USD notional and, at assumed 10 bps, 0.20002 USD fee. A later mark of
10,011 gives +0.20 USD unrealized PnL before other costs. Futures cash does not
pay spot principal; it changes by realized PnL and charges. Short PnL uses signed
contracts. `value_position` calculates notional and mark-to-market without placing
or enabling orders. Specs and scenarios expose stable canonical fingerprints.

Operational profiles can pin `native_contract_fingerprint(metadata)` under the
versioned NATIVE_CONTRACT_V1 scope: product/family/kind/currency/venue/size and
native increments/limits, plus dated expiry for BIT. Mutable quotes, funding,
open interest and session status stay in full raw provenance but do not redefine
the contract. Legacy full-response SHA pins remain supported. Every public poll
checks current structural metadata before storage; announced closed/disabled or
maintenance states halt. See [PAPER_QUICKSTART_FA.md](PAPER_QUICKSTART_FA.md).
