import asyncio
import logging
from typing import Optional

import aiohttp
import pandas as pd

from hummingbot.connector.constants import DAY, MINUTE
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.data_feed.candles_feed.btc_markets_candles import constants as CONSTANTS


class BtcMarketsCandles:
    """
    Candles feed for BTC Markets.

    This class fetches candlestick (OHLCV) data for a given trading pair
    and interval. It maintains a pandas DataFrame with the latest candles,
    which is periodically updated.
    """
    _logger = logging.getLogger(__name__)

    def __init__(self, trading_pair: str, interval: str = "1m", max_records: int = 200):
        self._trading_pair = trading_pair  # e.g., "BTC-AUD"
        self._interval = interval          # e.g., "1m", "1h", "1d"
        self._max_records = max_records
        self._candles_df: pd.DataFrame = pd.DataFrame()
        self._fetch_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def candles_df(self) -> pd.DataFrame:
        """
        The DataFrame containing the candle data.
        The 'timestamp' column is renamed to 'open_time' for consistency.
        """
        return self._candles_df.copy()

    async def start(self):
        """
        Starts the background task to fetch and update candle data.
        """
        if self._running:
            self._logger.warning(f"Candles feed for {self._trading_pair} is already running.")
            return
        self._running = True
        self._fetch_task = safe_ensure_future(self._fetch_candles_loop())
        self._logger.info(f"Started candles feed for {self._trading_pair} ({self._interval}).")

    def stop(self):
        """
        Stops the background candle fetching task.
        """
        if self._fetch_task:
            self._fetch_task.cancel()
            self._fetch_task = None
        self._running = False
        self._logger.info(f"Stopped candles feed for {self._trading_pair}.")

    async def _fetch_candles_loop(self):
        """
        Periodically fetches candle data from the BTC Markets API.
        """
        while self._running:
            try:
                await self._fetch_and_update_candles()
                # Sleep until the start of the next candle to be efficient
                await asyncio.sleep(self._seconds_until_next_candle())
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    f"Unexpected error fetching candles for {self._trading_pair}. Retrying in 30s."
                )
                await asyncio.sleep(30)

    async def _fetch_and_update_candles(self):
        """
        Fetches the latest candles from BTC Markets and updates the internal DataFrame.
        """
        url = f"{CONSTANTS.REST_URL}{CONSTANTS.CANDLES_ENDPOINT.format(marketId=self._trading_pair)}"
        params = {
            "timeWindow": self._interval,
            "limit": self._max_records
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status >= 400:
                    raise IOError(f"Error fetching candles from {url}. HTTP status: {response.status}")
                data = await response.json()

                new_df = pd.DataFrame(data, columns=CONSTANTS.CANDLE_COLUMNS)

                # Convert appropriate columns to numeric types
                numeric_cols = ["open", "high", "low", "close", "volume"]
                new_df[numeric_cols] = new_df[numeric_cols].apply(pd.to_numeric)

                # Convert ISO 8601 timestamp strings to datetime objects
                new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], utc=True)

                # Rename 'timestamp' to 'open_time' for internal consistency
                new_df.rename(columns={"timestamp": "open_time"}, inplace=True)

                self._candles_df = new_df
                self._logger.debug(f"Successfully fetched {len(self._candles_df)} candles for {self._trading_pair}.")

    def _seconds_until_next_candle(self) -> int:
        """
        Calculates the time in seconds until the next candle opens.
        """
        if self._candles_df.empty:
            return 1  # Wait 1 second if no data yet

        interval_seconds = self._interval_to_seconds()
        last_open_time_s = self._candles_df["open_time"].iloc[-1].timestamp()
        next_candle_open_time_s = last_open_time_s + interval_seconds
        now_s = pd.Timestamp.now(tz="UTC").timestamp()

        # Add a small buffer (e.g., 0.1s) to ensure the candle is closed
        return max(0, int(next_candle_open_time_s - now_s + 0.1))

    def _interval_to_seconds(self) -> int:
        """
        Converts interval string (e.g., '1m', '1h', '1d') to seconds.
        """
        unit = self._interval[-1]
        value = int(self._interval[:-1])
        if unit == 'm':
            return value * MINUTE
        if unit == 'h':
            return value * MINUTE * 60
        if unit == 'd':
            return value * DAY
        # Default to 60s if format is unexpected
        return MINUTE
