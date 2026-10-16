"""HTTP client behaviour: paths, headers, error mapping, retry policy (§4, §7, §8)."""

import json
from datetime import datetime

import pytest

from zw_compliance.fdms.client import FdmsClient, TransportResponse
from zw_compliance.fdms.errors import FdmsApiError, TransportError
from zw_compliance.fdms.types import DeviceIdentity

DEVICE = DeviceIdentity(321, "SN-001", "ERPNext", "v1")


class ScriptedTransport:
	def __init__(self, *responses):
		self.responses = list(responses)
		self.requests = []

	def request(self, req):
		self.requests.append(req)
		item = self.responses.pop(0)
		if isinstance(item, Exception):
			raise item
		return item


def ok(body, **headers):
	return TransportResponse(200, {k.lower(): v for k, v in headers.items()}, json.dumps(body))


def client(transport, **kw):
	return FdmsClient(DEVICE, transport, environment="test", **kw)


def test_paths_and_headers():
	t = ScriptedTransport(ok({"operationID": "o", "fiscalDayNo": 5}))
	day_no, op = client(t).open_day(datetime(2026, 10, 1, 8, 0, 0), 5)
	(req,) = t.requests
	assert req.url == "https://fdmsapitest.zimra.co.zw/Device/v1/321/OpenDay"
	assert req.headers["DeviceModelName"] == "ERPNext"
	assert req.headers["DeviceModelVersionNo"] == req.headers["DeviceModelVersion"] == "v1"
	assert json.loads(req.body) == {"fiscalDayOpened": "2026-10-01T08:00:00", "fiscalDayNo": 5}
	assert req.timeout == 30
	assert (day_no, op) == (5, "o")


def test_verify_taxpayer_information_is_public():
	t = ScriptedTransport(
		ok(
			{
				"operationID": "o",
				"taxPayerName": "Shop",
				"taxPayerTIN": "2000000001",
				"deviceBranchName": "HQ",
			}
		)
	)
	info = client(t).verify_taxpayer_information("ABCD1234")
	assert t.requests[0].url.endswith("/Public/v1/321/VerifyTaxpayerInformation")
	assert json.loads(t.requests[0].body) == {"activationKey": "ABCD1234", "deviceSerialNo": "SN-001"}
	assert info.taxpayer_name == "Shop"


def test_problem_details_are_mapped():
	t = ScriptedTransport(
		TransportResponse(
			422,
			{"operationid": "OP1:xyz"},
			json.dumps({"title": "Bad", "status": 422, "errorCode": "FISC01"}),
		)
	)
	with pytest.raises(FdmsApiError) as exc:
		client(t).open_day(datetime(2026, 1, 1), None)
	err = exc.value
	assert err.error_code == "FISC01"
	assert not err.retryable
	assert err.support_code == "FISC01-OP1"
	assert "previous fiscal day" in err.fix


def test_get_is_retried_on_network_error_but_submit_is_not(monkeypatch):
	monkeypatch.setattr("zw_compliance.fdms.client.time.sleep", lambda s: None)
	t = ScriptedTransport(
		TransportError("reset"), ok({"operationID": "o", "fiscalDayStatus": "FiscalDayClosed"})
	)
	assert client(t).get_status().fiscal_day_status == "FiscalDayClosed"
	assert len(t.requests) == 2

	t = ScriptedTransport(TransportError("reset"))
	with pytest.raises(TransportError):
		client(t).submit_receipt_payload({"receiptGlobalNo": 1})
	assert len(t.requests) == 1


def test_5xx_is_retryable_and_401_is_authentication():
	assert FdmsApiError(502).retryable
	assert FdmsApiError(500).retryable
	assert not FdmsApiError(400).retryable
	assert FdmsApiError(401).is_authentication_error
	assert "certificate" in FdmsApiError(401).fix


def test_server_time_is_reported():
	seen = []
	t = ScriptedTransport(
		ok({"operationID": "o", "reportingFrequency": 5}, Date="Wed, 01 Oct 2026 06:00:00 GMT")
	)
	assert client(t, on_server_time=seen.append).ping() == 5
	assert len(seen) == 1 and seen[0].round_trip_ms >= 0
