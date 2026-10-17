# Security & trading safety

- V1 is read-only. **Do not add live trade execution without separate review and explicit opt-in.**
- Never commit account numbers, passwords, API tokens, .env secrets, platform session files or private trading logs.
- Configure production credentials as encrypted environment secrets, not source files.
- Test strategies with isolated historical data, then paper-trading, then a demo account.
- Future execution modules must include a separate enable flag (default false), daily loss limits, maximum position/notional limits, account allowlist, emergency kill switch and audit logs.
- GitHub Actions must never be given broker login credentials for unit tests.
- MT5 trade volume, spread and available order-book depth vary by broker; do not assume exchange-quality market data.
