from unittest.mock import MagicMock, patch

import pytest
import requests

from src.capital_client import CapitalApiError
from src.retry import retry_with_backoff


def test_retry_with_backoff_succeeds_first_try_no_sleep():
    fn = MagicMock(return_value="ok")
    with patch("src.retry.time.sleep") as mock_sleep:
        result = retry_with_backoff(fn, exceptions=(CapitalApiError,))
    assert result == "ok"
    fn.assert_called_once()
    mock_sleep.assert_not_called()


def test_retry_with_backoff_succeeds_after_one_failure():
    fn = MagicMock(side_effect=[CapitalApiError("429"), "ok"])
    with patch("src.retry.time.sleep") as mock_sleep:
        result = retry_with_backoff(fn, exceptions=(CapitalApiError,))
    assert result == "ok"
    assert fn.call_count == 2
    mock_sleep.assert_called_once_with(1.0)


def test_retry_with_backoff_uses_successive_delays():
    fn = MagicMock(side_effect=[CapitalApiError("a"), CapitalApiError("b"), "ok"])
    with patch("src.retry.time.sleep") as mock_sleep:
        result = retry_with_backoff(fn, exceptions=(CapitalApiError,), attempts=3, delays_seconds=(1.0, 2.0))
    assert result == "ok"
    assert fn.call_count == 3
    assert mock_sleep.call_args_list == [((1.0,),), ((2.0,),)]


def test_retry_with_backoff_raises_last_exception_after_exhausting_attempts():
    exc1 = CapitalApiError("first")
    exc2 = CapitalApiError("second")
    fn = MagicMock(side_effect=[exc1, exc2])
    with patch("src.retry.time.sleep"):
        with pytest.raises(CapitalApiError) as excinfo:
            retry_with_backoff(fn, exceptions=(CapitalApiError,), attempts=2, delays_seconds=(1.0,))
    assert excinfo.value is exc2
    assert fn.call_count == 2


def test_retry_with_backoff_only_catches_listed_exceptions():
    fn = MagicMock(side_effect=ValueError("not retryable"))
    with patch("src.retry.time.sleep") as mock_sleep:
        with pytest.raises(ValueError):
            retry_with_backoff(fn, exceptions=(CapitalApiError,))
    fn.assert_called_once()
    mock_sleep.assert_not_called()


def test_retry_with_backoff_catches_request_exceptions_too():
    fn = MagicMock(side_effect=[requests.exceptions.ConnectionError("down"), "ok"])
    with patch("src.retry.time.sleep") as mock_sleep:
        result = retry_with_backoff(
            fn, exceptions=(CapitalApiError, requests.exceptions.RequestException),
        )
    assert result == "ok"
    mock_sleep.assert_called_once()


def test_retry_with_backoff_default_attempts_is_3():
    fn = MagicMock(side_effect=[CapitalApiError("a"), CapitalApiError("b"), "ok"])
    with patch("src.retry.time.sleep"):
        result = retry_with_backoff(fn, exceptions=(CapitalApiError,))
    assert result == "ok"
    assert fn.call_count == 3
