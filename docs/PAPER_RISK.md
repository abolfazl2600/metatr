# Versioned paper risk veto and persistent kill switch

`PaperRiskPolicy` binds account + full Market + immutable policy ID/version to
explicit simulated initial equity/currency, order/position notional, contract
quantity, gross/net exposure, leverage proxy, concurrency/order-per-bar limits,
cooldown, daily realized cash loss, drawdown and source freshness. Policy versions
are journaled as immutable risk events; conflicting same-version edits fail.
Create a new version for a changed policy, preserving all prior assessments.

This initial subsystem is one account/one market/one net position, one order per
closed bar, **fully collateralized hypothetical notional with leverage <= 1**.
No venue margin/liquidation calculation is claimed. The policy must explicitly
state `ASSUMED_FULLY_COLLATERALIZED` and written assumptions, and the contract
remains a scenario-only spec. Unsupported margin/currency/settlement/funding/specs
halt; the model's confidence cannot override them. Fully collateralized simulation
does not guarantee a bound on real futures gap losses.

`evaluate(intent, current_ledger, validated_latest_market, policy, contract, now)`
is a pure APPROVE/REJECT/HALT veto with stable reason codes, exact Decimal
post-order exposure, adverse tick-rounded price plus fee buffer, policy/data/ledger
fingerprints and an audit of assumptions. Reversals use post-trade net contracts
and order-level costs separately. NO_TRADE or a rejected assessment creates no fill.

Data checks require an intact ready report/calendar, exact contract/native
metadata, ordered closed bars, no unknown gap, no clock rollback, sane liquidity,
and source freshness. PUBLIC mode additionally verifies public provenance, as-of
availability and current sourced session coverage/open state. REPLAY is explicitly
historical simulation and may use later-collected backfill provenance; this is
never evidence that the data was available to a live trader at that past instant.

Risk uses current marked equity and prior equity peaks. Daily loss is net realized
cash change (realized PnL minus fees/assumed financing) in UTC, excluding simulated
opening capital; drawdown also sees unrealized loss. At the daily loss/drawdown
threshold, nonpositive equity, source failure or inconsistent ledger => HALT.
Size/order/exposure/cooldown/liquidity violations => REJECT. Duplicate order state
and conflicting per-bar intents are refused. Limits accept equality except daily
loss/drawdown circuit-breaker boundaries, which trip at equality.

`PaperRiskEngine.assess` pins the policy, reconciles persisted state, records the
assessment and latches HALT. Reboot never clears the latch. Reset is a separate
operator action with nonempty reason, current policy, journal reconciliation and
fresh source/account health. A stale feed or continuing loss breach refuses reset.
The store's low-level health flag is not an automatic reset authorization; use
the risk engine/runner operator flow. No exchange keys, orders or account API exists.

Tests cover exact at/below position/order/contract/gross/net limits, fee buffers,
stale/open/rollback, unsupported margin/currency, liquidity/step refusal, immutable
policies, loss/drawdown boundaries and halt→restart→healthy explicit reset.
