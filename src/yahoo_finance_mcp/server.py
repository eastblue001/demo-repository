"""MCP server exposing Yahoo Finance data through yfinance."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

import pandas as pd
import yfinance as yf
from mcp.server import MCPServer

from yahoo_finance_mcp import __version__

LOGGER = logging.getLogger(__name__)
SYMBOL_RE = re.compile(r"^[A-Za-z0-9^=._-]{1,32}$")
VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"}
VALID_INTERVALS = {
    "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"
}


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    stateless_http: bool = os.getenv("MCP_STATELESS_HTTP", "true").lower() == "true"
    json_response: bool = os.getenv("MCP_JSON_RESPONSE", "false").lower() == "true"
    max_history_rows: int = int(os.getenv("MAX_HISTORY_ROWS", "5000"))
    request_timeout: float = float(os.getenv("YF_TIMEOUT_SECONDS", "20"))


SETTINGS = Settings()
mcp = MCPServer(
    "Yahoo Finance MCP",
    version=__version__,
    instructions=(
        "Use these tools to retrieve current quotes, historical prices, company data, "
        "financial statements, news, and option chains from Yahoo Finance. "
        "Market data may be delayed and must not be treated as investment advice."
    ),
    log_level=SETTINGS.log_level,
)


def _ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def _error(code: str, message: str) -> dict[str, Any]:
    LOGGER.warning("Yahoo Finance request failed [%s]: %s", code, message)
    return {"ok": False, "error": {"code": code, "message": message}}


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(normalized):
        raise ValueError(
            "symbol must be 1-32 characters and contain only letters, numbers, ^, =, ., _, or -"
        )
    return normalized


def _validate_iso_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD format") from exc
    return value


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            "_".join(str(part) for part in column if str(part) not in {"", "None"})
            for column in result.columns
        ]
    else:
        result.columns = [str(column) for column in result.columns]
    return result


def _dataframe_records(frame: pd.DataFrame, index_name: str = "Date") -> list[dict[str, Any]]:
    result = _flatten_columns(frame)
    result.index.name = result.index.name or index_name
    result = result.reset_index()
    return [
        {str(column): _json_value(value) for column, value in row.items()}
        for row in result.to_dict(orient="records")
    ]


def _get_quote_impl(symbol: str) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        ticker = yf.Ticker(normalized)
        fast_fields = {
            "currency": "currency",
            "exchange": "exchange",
            "timezone": "timezone",
            "last_price": "last_price",
            "previous_close": "previous_close",
            "open": "open",
            "day_high": "day_high",
            "day_low": "day_low",
            "year_high": "year_high",
            "year_low": "year_low",
            "market_cap": "market_cap",
            "shares": "shares",
            "ten_day_average_volume": "ten_day_average_volume",
            "three_month_average_volume": "three_month_average_volume",
        }
        quote: dict[str, Any] = {}
        fast_info = ticker.fast_info
        for output_name, source_name in fast_fields.items():
            try:
                quote[output_name] = _json_value(fast_info[source_name])
            except (KeyError, TypeError, AttributeError):
                continue

        info: dict[str, Any] = {}
        try:
            raw_info = ticker.info
            for field in (
                "shortName",
                "longName",
                "quoteType",
                "sector",
                "industry",
                "website",
                "country",
            ):
                if field in raw_info:
                    info[field] = _json_value(raw_info[field])
        except Exception as exc:  # noqa: BLE001 - partial quote is still useful
            LOGGER.info("Metadata lookup failed for %s: %s", normalized, exc)

        if not quote and not info:
            return _error("no_data", f"No quote data was returned for {normalized}")
        return _ok(
            symbol=normalized,
            as_of=datetime.now(UTC).isoformat(),
            quote=quote,
            company=info,
        )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001 - convert provider failures into structured MCP output
        LOGGER.exception("Quote lookup failed")
        return _error("provider_error", str(exc))


def _get_history_impl(
    symbol: str,
    period: str | None = "1mo",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    auto_adjust: bool = True,
    prepost: bool = False,
    actions: bool = False,
    max_rows: int = 1000,
) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        start = _validate_iso_date(start, "start")
        end = _validate_iso_date(end, "end")
        if start and end and date.fromisoformat(start) >= date.fromisoformat(end):
            raise ValueError("start must be earlier than end")
        if (start or end) and period not in {None, ""}:
            raise ValueError("Use period or start/end, not both; set period to null with start/end")
        if not start and not end:
            period = period or "1mo"
            if period not in VALID_PERIODS:
                raise ValueError(f"period must be one of: {', '.join(sorted(VALID_PERIODS))}")
        if interval not in VALID_INTERVALS:
            raise ValueError(f"interval must be one of: {', '.join(sorted(VALID_INTERVALS))}")
        if not 1 <= max_rows <= SETTINGS.max_history_rows:
            raise ValueError(f"max_rows must be between 1 and {SETTINGS.max_history_rows}")

        kwargs: dict[str, Any] = {
            "tickers": normalized,
            "interval": interval,
            "auto_adjust": auto_adjust,
            "prepost": prepost,
            "actions": actions,
            "progress": False,
            "threads": False,
            "timeout": SETTINGS.request_timeout,
            "multi_level_index": False,
        }
        if start or end:
            kwargs.update(start=start, end=end)
        else:
            kwargs["period"] = period

        frame = yf.download(**kwargs)
        if frame is None or frame.empty:
            return _error("no_data", f"No historical data was returned for {normalized}")
        total_rows = len(frame)
        truncated = total_rows > max_rows
        if truncated:
            frame = frame.tail(max_rows)
        return _ok(
            symbol=normalized,
            request={
                "period": period if not (start or end) else None,
                "start": start,
                "end": end,
                "interval": interval,
                "auto_adjust": auto_adjust,
                "prepost": prepost,
                "actions": actions,
            },
            row_count=len(frame),
            total_rows=total_rows,
            truncated=truncated,
            data=_dataframe_records(frame),
        )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("History lookup failed")
        return _error("provider_error", str(exc))


def _get_company_profile_impl(symbol: str) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        info = yf.Ticker(normalized).info
        if not info:
            return _error("no_data", f"No company profile was returned for {normalized}")
        fields = (
            "shortName",
            "longName",
            "symbol",
            "quoteType",
            "exchange",
            "currency",
            "sector",
            "industry",
            "country",
            "city",
            "website",
            "fullTimeEmployees",
            "longBusinessSummary",
            "marketCap",
            "enterpriseValue",
            "trailingPE",
            "forwardPE",
            "dividendYield",
            "beta",
        )
        return _ok(
            symbol=normalized,
            profile={field: _json_value(info[field]) for field in fields if field in info},
        )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Company profile lookup failed")
        return _error("provider_error", str(exc))


def _get_financial_statement_impl(
    symbol: str,
    statement: Literal["income", "balance_sheet", "cash_flow"] = "income",
    frequency: Literal["annual", "quarterly"] = "annual",
    max_periods: int = 4,
) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        if not 1 <= max_periods <= 8:
            raise ValueError("max_periods must be between 1 and 8")
        ticker = yf.Ticker(normalized)
        frequency_arg = "yearly" if frequency == "annual" else "quarterly"
        methods = {
            "income": ticker.get_income_stmt,
            "balance_sheet": ticker.get_balance_sheet,
            "cash_flow": ticker.get_cash_flow,
        }
        frame = methods[statement](freq=frequency_arg)
        if frame is None or frame.empty:
            return _error("no_data", f"No {statement} data was returned for {normalized}")
        frame = frame.iloc[:, :max_periods].T
        frame.index.name = "period"
        return _ok(
            symbol=normalized,
            statement=statement,
            frequency=frequency,
            periods=len(frame),
            data=_dataframe_records(frame, index_name="period"),
        )
    except (KeyError, ValueError) as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Financial statement lookup failed")
        return _error("provider_error", str(exc))


def _get_news_impl(symbol: str, limit: int = 10) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        news = yf.Ticker(normalized).news or []
        return _ok(symbol=normalized, count=min(len(news), limit), news=_json_value(news[:limit]))
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("News lookup failed")
        return _error("provider_error", str(exc))


def _get_options_impl(
    symbol: str,
    expiration: str | None = None,
    max_contracts_per_side: int = 100,
) -> dict[str, Any]:
    try:
        normalized = _normalize_symbol(symbol)
        if not 1 <= max_contracts_per_side <= 500:
            raise ValueError("max_contracts_per_side must be between 1 and 500")
        ticker = yf.Ticker(normalized)
        expirations = list(ticker.options or [])
        if expiration is None:
            return _ok(symbol=normalized, expirations=expirations)
        _validate_iso_date(expiration, "expiration")
        if expiration not in expirations:
            return _error(
                "invalid_argument",
                (
                    f"expiration {expiration} is unavailable; call without expiration "
                    "to list valid dates"
                ),
            )
        chain = ticker.option_chain(expiration)
        calls = chain.calls.head(max_contracts_per_side)
        puts = chain.puts.head(max_contracts_per_side)
        return _ok(
            symbol=normalized,
            expiration=expiration,
            calls_count=len(calls),
            puts_count=len(puts),
            calls=_dataframe_records(calls, index_name="row"),
            puts=_dataframe_records(puts, index_name="row"),
        )
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Options lookup failed")
        return _error("provider_error", str(exc))


def _search_tickers_impl(query: str, limit: int = 10) -> dict[str, Any]:
    try:
        query = query.strip()
        if not 1 <= len(query) <= 100:
            raise ValueError("query must be between 1 and 100 characters")
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        search = yf.Search(query, max_results=limit, news_count=0)
        quotes = list(search.quotes or [])[:limit]
        return _ok(query=query, count=len(quotes), results=_json_value(quotes))
    except ValueError as exc:
        return _error("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Ticker search failed")
        return _error("provider_error", str(exc))


@mcp.tool()
def get_quote(symbol: str) -> dict[str, Any]:
    """Get the latest available quote and basic company identity for a Yahoo ticker."""
    return _get_quote_impl(symbol)


@mcp.tool()
def get_history(
    symbol: str,
    period: str | None = "1mo",
    interval: str = "1d",
    start: str | None = None,
    end: str | None = None,
    auto_adjust: bool = True,
    prepost: bool = False,
    actions: bool = False,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Get OHLCV history. Use period, or set period=null and supply YYYY-MM-DD start/end."""
    return _get_history_impl(
        symbol, period, interval, start, end, auto_adjust, prepost, actions, max_rows
    )


@mcp.tool()
def get_company_profile(symbol: str) -> dict[str, Any]:
    """Get company profile, valuation, sector, industry, and business summary."""
    return _get_company_profile_impl(symbol)


@mcp.tool()
def get_financial_statement(
    symbol: str,
    statement: Literal["income", "balance_sheet", "cash_flow"] = "income",
    frequency: Literal["annual", "quarterly"] = "annual",
    max_periods: int = 4,
) -> dict[str, Any]:
    """Get an annual or quarterly income statement, balance sheet, or cash-flow statement."""
    return _get_financial_statement_impl(symbol, statement, frequency, max_periods)


@mcp.tool()
def get_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Get recent Yahoo Finance news items for a ticker."""
    return _get_news_impl(symbol, limit)


@mcp.tool()
def get_options(
    symbol: str,
    expiration: str | None = None,
    max_contracts_per_side: int = 100,
) -> dict[str, Any]:
    """List option expiration dates, or get calls and puts for one YYYY-MM-DD expiration."""
    return _get_options_impl(symbol, expiration, max_contracts_per_side)


@mcp.tool()
def search_tickers(query: str, limit: int = 10) -> dict[str, Any]:
    """Search Yahoo Finance for ticker symbols by company, index, fund, or asset name."""
    return _search_tickers_impl(query, limit)


def main() -> None:
    """Run the MCP server with the Streamable HTTP transport."""
    LOGGER.info(
        "Starting Yahoo Finance MCP on %s:%s/mcp (stateless=%s)",
        SETTINGS.host,
        SETTINGS.port,
        SETTINGS.stateless_http,
    )
    mcp.run(
        transport="streamable-http",
        host=SETTINGS.host,
        port=SETTINGS.port,
        streamable_http_path="/mcp",
        stateless_http=SETTINGS.stateless_http,
        json_response=SETTINGS.json_response,
        max_request_body_size=1 * 1024 * 1024,
    )


if __name__ == "__main__":
    main()
