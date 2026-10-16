"""Scheduled maintenance: day-end notice and automatic close (§9 req. 4, 5),
ping (§4.13) and certificate care (§9 req. 16)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime

from zw_compliance.fiscalisation import schedule

from .fixtures import ITEM, FiscalisationTestCase, make_invoice


class SchedulerTestCase(FiscalisationTestCase):
	def setUp(self):
		self.notices = []
		recorder = patch.object(
			schedule,
			"notify_managers",
			lambda device, subject, message, reference=None: self.notices.append(subject),
		)
		recorder.start()
		self.addCleanup(recorder.stop)

	def settings(self, **values):
		doc = frappe.get_doc("Zimra Settings")
		doc.update(values)
		return doc


class TestDayMaintenance(SchedulerTestCase):
	def _open_day(self, opened_hours_ago: float):
		invoice = make_invoice([(ITEM, 1, 10)])
		receipt = self.receipt_of(invoice)
		self.sync()  # closes a day an earlier test left closing, then opens this one
		self.fdms.settle()
		self.sync()
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", receipt.fiscal_day, "status"), "Opened")
		frappe.db.set_value(
			"Zimra Fiscal Day",
			receipt.fiscal_day,
			{"fiscal_day_opened": now_datetime() - timedelta(hours=opened_hours_ago), "end_notice_sent": 0},
		)
		frappe.db.set_value(
			"Zimra Fiscal Device", self.device.name, {"day_max_hours": 24, "day_end_notification_hours": 2}
		)
		return receipt.fiscal_day

	def test_notice_before_the_day_end_is_sent_once(self):
		day = self._open_day(opened_hours_ago=22.5)
		settings = self.settings(close_margin_hours=0, auto_close_time=None)
		schedule._maintain_device(self.device.name, settings, now_datetime())
		schedule._maintain_device(self.device.name, settings, now_datetime())
		self.assertEqual(len([n for n in self.notices if "ends at" in n]), 1)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", day, "status"), "Opened")

	def test_day_is_closed_before_the_maximum_length(self):
		day = self._open_day(opened_hours_ago=23.5)
		schedule._maintain_device(self.device.name, self.settings(close_margin_hours=1), now_datetime())
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", day, "status"), "Close Requested")

	def test_day_is_closed_at_the_configured_time(self):
		day = self._open_day(opened_hours_ago=3)
		now = now_datetime()
		close_at = (now - timedelta(minutes=5)).time().replace(microsecond=0)
		if (now - timedelta(minutes=5)).date() != now.date():
			self.skipTest("too close to midnight")
		schedule._maintain_device(
			self.device.name, self.settings(auto_close_time=close_at, close_margin_hours=0), now
		)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", day, "status"), "Close Requested")

	def test_day_opened_after_the_close_time_is_left_open(self):
		day = self._open_day(opened_hours_ago=0.02)
		now = now_datetime()
		close_at = (now - timedelta(hours=1)).time().replace(microsecond=0)
		if (now - timedelta(hours=1)).date() != now.date():
			self.skipTest("too close to midnight")
		schedule._maintain_device(
			self.device.name, self.settings(auto_close_time=close_at, close_margin_hours=0), now
		)
		self.assertEqual(frappe.db.get_value("Zimra Fiscal Day", day, "status"), "Opened")


class TestPing(SchedulerTestCase):
	def test_ping_records_the_reporting_frequency(self):
		from zw_compliance.fiscalisation.device import ping

		self.assertEqual(ping(self.device.name), 5)
		values = frappe.db.get_value(
			"Zimra Fiscal Device",
			self.device.name,
			["last_ping", "next_ping_at", "reporting_frequency"],
			as_dict=True,
		)
		self.assertEqual(values.reporting_frequency, 5)
		self.assertAlmostEqual((values.next_ping_at - values.last_ping).total_seconds(), 300, delta=1)

	def test_every_minute_pings_only_when_due(self):
		calls = []
		with patch.object(frappe, "enqueue", lambda method, **kw: calls.append(method)):
			frappe.db.set_value(
				"Zimra Fiscal Device", self.device.name, "next_ping_at", add_days(now_datetime(), 1)
			)
			schedule.every_minute()
			self.assertNotIn("zw_compliance.fiscalisation.device.ping", calls)
			frappe.db.set_value("Zimra Fiscal Device", self.device.name, "next_ping_at", None)
			schedule.every_minute()
			self.assertIn("zw_compliance.fiscalisation.device.ping", calls)


class TestCertificateCare(SchedulerTestCase):
	def _expires_in(self, days: int):
		frappe.db.set_value(
			"Zimra Fiscal Device", self.device.name, "certificate_valid_till", add_days(now_datetime(), days)
		)

	def test_no_warning_while_far_from_expiry(self):
		self._expires_in(200)
		schedule._certificate_care(self.device.name, self.settings(certificate_warning_days=30))
		self.assertEqual(self.notices, [])

	def test_warning_when_close_to_expiry(self):
		self._expires_in(10)
		schedule._certificate_care(
			self.device.name, self.settings(certificate_warning_days=30, auto_renew_certificate=0)
		)
		self.assertTrue(any("expires in" in n for n in self.notices), self.notices)

	def test_automatic_renewal_issues_a_new_certificate_and_key(self):
		before = frappe.get_doc("Zimra Fiscal Device", self.device.name)
		old_certificate, old_key = before.certificate, before.get_password("private_key")
		self._expires_in(10)
		schedule._certificate_care(
			self.device.name, self.settings(certificate_warning_days=30, auto_renew_certificate=1)
		)
		after = frappe.get_doc("Zimra Fiscal Device", self.device.name)
		self.assertNotEqual(after.certificate, old_certificate)
		self.assertNotEqual(after.get_password("private_key"), old_key)
		self.assertGreater(after.certificate_valid_till, add_days(now_datetime(), 300))
		self.assertTrue(any("renewed" in n for n in self.notices), self.notices)
		# the renewed identity is accepted by FDMS
		invoice = make_invoice([(ITEM, 1, 10)])
		self.sync()
		self.assertEqual(self.receipt_of(invoice).sync_status, "Submitted")

	def test_expired_certificate_is_reported(self):
		self._expires_in(-1)
		schedule._certificate_care(self.device.name, self.settings(certificate_warning_days=30))
		self.assertTrue(any("expired" in n for n in self.notices), self.notices)
