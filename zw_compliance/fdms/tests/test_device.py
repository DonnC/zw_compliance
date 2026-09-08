from zw_compliance.fdms.device import FiscalDevice, FiscalDeviceOptions, PendingSubmitError
from zw_compliance.fdms.errors import FdmsApiError
from zw_compliance.fdms.queue import OfflineReceiptQueue
from zw_compliance.fdms.storage import guarded_storage
from zw_compliance.fdms.types import DeviceIdentity, PaymentInput, ReceiptInput, ReceiptLineInput

from .fakes import FakeFdmsServer, FakeSigner, FakeTransport, MemoryStorage


def _device(server=None, storage=None) -> tuple[FiscalDevice, FakeFdmsServer]:
	server = server or FakeFdmsServer()
	identity = DeviceIdentity(device_id=37367, serial_number="TEST01", model_name="Server", model_version="v1")
	device = FiscalDevice(
		identity,
		FakeSigner(),
		FakeTransport(server),
		storage or MemoryStorage(),
		options=FiscalDeviceOptions(environment="test"),
	)
	return device, server


def _sample_receipt(invoice_no="INV-0001") -> ReceiptInput:
	return ReceiptInput(
		currency="USD",
		invoice_no=invoice_no,
		lines=[ReceiptLineInput(name="Widget", price=115, quantity=1, tax_id=1, tax_percent=15)],
		payments=[PaymentInput(money_type="Cash", amount=115)],
	)


def test_open_day_submit_receipt_close_day_happy_path():
	device, server = _device()
	device.open_day()
	assert device.state.fiscal_day_no == server.fiscal_day_no

	submitted = device.submit_receipt(_sample_receipt())
	assert submitted.response["receiptID"] == 1
	assert device.state.receipt_global_no == 1
	assert device.state.receipt_counter == 1
	assert device.state.previous_receipt_hash == submitted.receipt.receipt_device_signature.hash

	# second receipt chains off the first
	second = device.submit_receipt(_sample_receipt("INV-0002"))
	assert second.receipt.receipt_global_no == 2

	device.close_day()
	assert device.state is None


def test_receipt_global_no_survives_across_days_max_of_persisted_and_server():
	storage = MemoryStorage()
	device, server = _device(storage=storage)
	device.open_day()
	device.submit_receipt(_sample_receipt())
	device.close_day()

	# reopen: a fresh device instance (simulating a new worker process)
	# restored from the same storage must not reuse global no 1.
	device2, _ = _device(server=server, storage=storage)
	device2.open_day()
	assert device2.state.receipt_global_no == 1  # picks up persisted last-issued-global-no
	submitted = device2.submit_receipt(_sample_receipt("INV-0003"))
	assert submitted.receipt.receipt_global_no == 2


def test_pending_submit_blocks_further_calls_until_reconciled():
	storage = MemoryStorage()
	server = FakeFdmsServer()
	device, _ = _device(server, storage)
	device.open_day()

	server.drop_next_submit_response = True
	try:
		device.submit_receipt(_sample_receipt())
		raise AssertionError("expected a simulated dropped-connection error")
	except Exception:
		pass

	# a pending marker was written before the network call, so a second
	# device instance sharing storage must refuse to issue another receipt
	device2, _ = _device(server, storage)
	device2.load_persisted_state()
	try:
		device2.submit_receipt(_sample_receipt("INV-0002"))
		raise AssertionError("expected PendingSubmitError")
	except PendingSubmitError:
		pass

	result = device2.reconcile()
	assert result.action in ("confirmed", "resubmitted")
	# now a new receipt can be issued
	submitted = device2.submit_receipt(_sample_receipt("INV-0003"))
	assert submitted.receipt.receipt_global_no == 2


def test_reconcile_marks_rejected_receipt_and_clears_pending():
	storage = MemoryStorage()
	server = FakeFdmsServer()
	device, _ = _device(server, storage)
	device.open_day()
	server.fail_next_submit_before_receiving = True
	try:
		device.submit_receipt(_sample_receipt())
	except Exception:
		pass

	server.reject_next_submit = True
	result = device.reconcile()
	assert result.action == "rejected"
	assert device.pending_receipt() is None


def test_offline_queue_journals_on_network_failure_then_flushes():
	storage = MemoryStorage()
	server = FakeFdmsServer()
	device, _ = _device(server, storage)
	device.open_day()

	from zw_compliance.fdms.journal import MemoryJournal

	queue = OfflineReceiptQueue(device, MemoryJournal())
	server.drop_next_submit_response = True
	result = queue.submit_or_enqueue(_sample_receipt())
	assert result is None
	assert queue.size == 1

	flush_result = queue.flush()
	assert flush_result.remaining == 0
	assert len(flush_result.submitted) == 1
	assert queue.size == 0
