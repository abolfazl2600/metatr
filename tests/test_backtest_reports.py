from dataclasses import replace
from decimal import Decimal

import pytest
from test_validation import inputs

from metatr.backtest_reports import build_report, export_report, metrics
from metatr.validation import walk_forward


def test_independent_golden_equity_metrics():
    result = {
        "risk": {"initial_equity": "100"},
        "status": "COMPLETE",
        "equity": [{"equity": v, "contracts": "1"} for v in ("110", "99", "120")],
        "trades": [{"net_pnl": "15"}, {"net_pnl": "-5"}],
        "ledger": {"contracts": "0", "fees": "2", "funding": "1"},
        "fills": [{"notional": "100"}, {"notional": "110"}],
    }
    values = metrics(result, 252)
    assert values["net_return"] == 0.2
    assert values["max_drawdown"] == 0.1
    assert values["max_drawdown_duration_bars"] == 1
    assert values["profit_factor"] == 3
    assert values["fee_burden"] == 0.03 and values["turnover"] == 2.1
    flat = {**result, "trades": [], "equity": [{"equity": "100", "contracts": "0"}] * 3}
    assert metrics(flat, 252)["sharpe"] is None
    assert metrics(flat, 252)["sortino"] is None
    assert metrics(flat, 252)["profit_factor"] is None
    assert metrics(flat, 252)["inconclusive"]
    negative = {**result, "equity": [{"equity": "-1", "contracts": "1"}]}
    assert metrics(negative, 252)["net_return"] is None


def test_reproducible_reports_stress_and_immutable_exports(tmp_path):
    args = inputs(tmp_path)
    validation = walk_forward(*args)
    dataset, specs, contract, costs, risk, config = args
    risk = replace(risk, max_holding_bars=config.holding_horizon)

    def report():
        return build_report(
            validation,
            dataset,
            specs,
            contract,
            costs,
            risk,
            annual_bars=1000,
            stress_scenarios=[replace(costs, taker_fee_bps="20")],
        )

    a, b = report(), report()
    assert a == b
    body = a.to_dict()
    total = sum(Decimal(f["test"]["net_pnl"]) for f in body["fold_metrics"])
    assert Decimal(body["aggregate_oos"]["net_pnl_sum"]) == total
    assert len(body["cost_stress"]) == len(body["fold_metrics"])
    target = export_report(a, tmp_path / "reports")
    assert target == export_report(b, tmp_path / "reports")
    assert (target / "trades.csv").exists() and (target / "equity.csv").exists()
    (target / "trades.csv").write_text("corrupt")
    with pytest.raises(ValueError):
        export_report(a, tmp_path / "reports")


def test_cli_real_store_registry_pipeline(tmp_path, monkeypatch):
    import json
    import socket
    from dataclasses import asdict

    from metatr.backtest_cli import main
    from metatr.model_registry import StrategyRegistry

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    dataset, specs, contract, costs, risk, config = inputs(tmp_path)
    registry = StrategyRegistry(tmp_path / "registry.sqlite")
    registry.register_many(specs)
    bodies = {"contract": {**asdict(contract), "market": contract.market.key},
              "costs": asdict(costs), "risk": asdict(risk), "folds": asdict(config),
              "calendar": json.loads(dataset.validation_json)["calendar"]}
    bodies["calendar"]["market"] = dataset.market.key
    for key, value in bodies.items():
        (tmp_path / f"{key}.json").write_text(json.dumps(value))
    args = ["--store", str(tmp_path / "backtest.sqlite"), "--registry", str(registry.path),
            "--market", dataset.market.key, "--start-utc", dataset.start_utc,
            "--end-utc", dataset.end_utc, "--asof-utc", dataset.asof_utc,
            "--output", str(tmp_path / "cli-reports"), "--strategy", "momentum@1.0.0",
            "--annual-bars", "1000"]
    for flag, key in [("contract-spec", "contract"), ("cost-scenario", "costs"),
                      ("risk-policy", "risk"), ("fold-config", "folds"), ("calendar", "calendar")]:
        args.extend([f"--{flag}", str(tmp_path / f"{key}.json")])
    assert main(args) == 0
    assert main(args) == 0
    registry.retire(dataset.market, "momentum", "1.0.0")
    assert main(args) == 1
