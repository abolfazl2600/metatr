"""Publish the latest *committed* simulated Paper signal, never an order.

Paper strategies pin a per-bar validated prefix. Display manifests pin a full
snapshot. Earlier bar prefixes may differ; the latest full prefix often matches.
Verify both rather
than relaxing either validation contract or reusing an unverified decision.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from metatr.futures_store import TIMEFRAME_SECONDS
from metatr.mt5_publisher import publish, read_publication
from metatr.research import digest, write_experiment
from metatr.strategies import StrategyContext, predict, utc
from metatr.validation import slice_dataset


def publish_paper_display(dataset, decision, spec, contract, result, output_dir, *,
                          now_utc, evidence_dir):
    """Publish a one-marker *research event*, after proof of the Paper checkpoint.

    Do not turn simulated fills, accepted intents, or account equity into an
    MQL5 trade. The MQL5 protocol remains execution=DISABLED and validated.
    """
    if result["halted"] or result["processed_bars"] <= 0:
        raise ValueError("Only freshly committed, non-halted Paper bars can display")
    dataset.verify()
    step = pd.Timedelta(seconds=TIMEFRAME_SECONDS[dataset.market.timeframe])
    latest = dataset.bars.index[-1]
    closed = latest + step
    if (utc(result["last_committed_candle_close_utc"]) != closed
            or utc(decision.bar_open_utc) != latest
            or utc(decision.timestamp) != closed
            or decision.market != dataset.market.key
            or decision.strategy_id != spec.strategy_id
            or decision.strategy_version != spec.version
            or decision.config_sha256 != spec.config_sha256
            or decision.execution != "DISABLED"
            or decision.semantics != "event_signal"
            or not decision.reason.startswith("PAPER_SIMULATED ")):
        raise ValueError("Paper display decision/checkpoint/model mismatch")
    prefix = slice_dataset(dataset, 0, len(dataset.bars))
    if (decision.data_sha256 != prefix.data_sha256
            or tuple(decision.snapshot_ids) != prefix.snapshot_ids):
        raise ValueError("Paper decision prefix provenance mismatch")
    expected = predict(prefix.bars, spec, StrategyContext(
        dataset.market, latest, closed, utc(dataset.asof_utc),
        prefix.snapshot_ids, prefix.data_sha256, spec.warmup_bars, 0,
    ))
    veto_reason = "PAPER_SIMULATED PAPER_SIMULATED_RISK_VETO"
    if decision.reason == veto_reason:
        if decision.action != "NO_TRADE" or expected.action not in ("LONG", "SHORT"):
            raise ValueError("Paper veto cannot be relabeled as an actionable signal")
        veto = True
    else:
        if (decision.reason != "PAPER_SIMULATED " + expected.reason
                or decision.action != expected.action):
            raise ValueError("Paper decision differs from pinned strategy event")
        veto = False
    if veto:
        display_reason = "PAPER_SIMULATED_RISK_VETO_NO_ORDER"
    else:
        display_reason = "PAPER_SIMULATED_SIGNAL_NOT_ORDER " + expected.reason
    display = replace(
        decision, reason=display_reason, data_sha256=dataset.data_sha256,
        snapshot_ids=dataset.snapshot_ids,
    )
    manifest = publish(dataset, [display], spec, contract, Path(output_dir), now_utc=now_utc)
    verified = read_publication(manifest.parent, dataset.market, now_utc=now_utc)
    if verified["signals"][-1]["action"] != display.action:
        raise ValueError("Published Paper display signal differs from verified handoff")
    body = {
        "schema_version": 1,
        "kind": "PAPER_DISPLAY_HANDOFF",
        "label": "PAPER_ONLY_SIMULATED",
        "market": dataset.market.key,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.version,
        "config_sha256": spec.config_sha256,
        "original_prefix_sha256": prefix.data_sha256,
        "display_dataset_sha256": dataset.data_sha256,
        "paper_action": decision.action,
        "source_signal_action": expected.action,
        "display_action": display.action,
        "risk_veto": veto,
        "paper_checkpoint_utc": closed.isoformat(),
        "display_run_id": verified["manifest"]["run_id"],
        "manifest_sha256": digest(verified["manifest"]),
        "display_execution": "DISABLED",
        "order_execution": "PAPER_ONLY_SIMULATED",
        "native_mt5_acceptance": "PENDING_MANUAL_EVIDENCE",
    }
    evidence = write_experiment({"run_id": digest(body), **body}, Path(evidence_dir))
    return {"status": "PUBLISHED_RESEARCH_SIGNAL_NOT_ORDER",
            "manifest": str(manifest), "evidence": str(evidence),
            "run_id": verified["manifest"]["run_id"]}
