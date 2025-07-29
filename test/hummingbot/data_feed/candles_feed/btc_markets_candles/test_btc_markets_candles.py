import asyncio
import unittest
from unittest.mock import patch

from hummingbot.connector.test_support.network_mocking_assistant import NetworkMockingAssistant
from hummingbot.data_feed.candles_feed.btc_markets_candles.btc_markets_candles import BtcMarketsCandles


class TestBtcMarketsCandles(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.base_asset = "BTC"
        cls.quote_asset = "AUD"
        cls.interval = "1m"
        cls.trading_pair = f"{cls.base_asset}-{cls.quote_asset}"
        cls.max_records = 3

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.data_feed = BtcMarketsCandles(
            trading_pair=self.trading_pair,
            interval=self.interval,
            max_records=self.max_records
        )
        self.mocking_assistant = NetworkMockingAssistant()
        self.resume_test_event = asyncio.Event()

    def tearDown(self) -> None:
        if self.data_feed._running:
            self.data_feed.stop()
        super().tearDown()

    def get_candles_rest_data_mock(self):
        """
        Returns a mock candle data in the format of the BTC Markets API.
        """
        return [
            ["2024-06-18T00:02:00.000Z", "66150.7", "66180.8", "66100.9", "66120.0", "8.2"],
            ["2024-06-18T00:01:00.000Z", "66050.4", "66200.5", "66020.6", "66150.7", "12.8"],
            ["2024-06-18T00:00:00.000Z", "66000.1", "66100.2", "65900.3", "66050.4", "10.5"],
        ]

    def _get_async_sleep_patch(self):
        """
        A patch for asyncio.sleep that sets an event to resume the test and then
        hangs the background task indefinitely to prevent further loop iterations.
        """
        async def async_sleep_patch(delay: float):
            if not self.resume_test_event.is_set():
                self.resume_test_event.set()
            # Create a future that never completes to pause the loop.
            await asyncio.Future()
        return async_sleep_patch

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_candles_loop_exception(self, get_mock):
        # Configure the mock to be handled by the mocking assistant
        self.mocking_assistant.configure_http_request_mock(get_mock)

        # Add the desired response to the queue for the mock
        self.mocking_assistant.add_http_response(
            http_request_mock=get_mock,
            response_status=404,
            response_text='{"error": "Not Found"}'
        )

        fetch_task = None
        with patch("asyncio.sleep", new_callable=self._get_async_sleep_patch):
            await self.data_feed.start()
            await self.resume_test_event.wait()
            fetch_task = self.data_feed._fetch_task

        # The patch is no longer active. Now we can stop and yield.
        self.data_feed.stop()
        await asyncio.sleep(0)

        self.assertEqual(0, len(self.data_feed.candles_df))
        self.assertTrue(fetch_task.cancelled())

    @patch("aiohttp.ClientSession.get")
    async def test_start_and_stop_candles_feed(self, get_mock):
        # Configure the mock to be handled by the mocking assistant
        self.mocking_assistant.configure_http_request_mock(get_mock)

        # Add the desired response to the queue for the mock
        self.mocking_assistant.add_http_response(
            http_request_mock=get_mock,
            response_status=200,
            response_json=self.get_candles_rest_data_mock()
        )

        fetch_task = None
        with patch("asyncio.sleep", new_callable=self._get_async_sleep_patch):
            await self.data_feed.start()
            await self.resume_test_event.wait()
            # Capture the task while the patch is active and the task is paused
            fetch_task = self.data_feed._fetch_task

        # The patch is no longer active. Now we can stop and yield.
        self.data_feed.stop()
        await asyncio.sleep(0)

        candles_df = self.data_feed.candles_df

        # The object is a DataFrame, but we test its properties without pandas-specific functions
        self.assertEqual("DataFrame", type(candles_df).__name__)
        self.assertGreater(len(candles_df), 0)
        self.assertEqual(self.max_records, len(candles_df))

        # Check columns
        expected_columns = ["open_time", "open", "high", "low", "close", "volume"]
        self.assertListEqual(expected_columns, list(candles_df.columns))

        # Check data types by inspecting the first element
        first_row = candles_df.iloc[0]
        self.assertEqual("Timestamp", type(first_row["open_time"]).__name__)
        self.assertIsInstance(first_row["open"], float)

        # Check a sample value
        self.assertEqual(66150.7, first_row["open"])

        # The task should be cancelled
        self.assertTrue(fetch_task.cancelled())
