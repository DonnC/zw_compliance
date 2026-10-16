app_name = "zw_compliance"
app_title = "Zimbabwe Compliance"
app_publisher = "Donald Chinhuru"
app_description = (
	"Zimbabwe compliance for ERPNext: ZIMRA fiscalisation, with payroll and statutory returns to follow"
)
app_email = "donnclab@gmail.com"
app_license = "mit"

required_apps = ["erpnext"]

app_logo_url = "/assets/zw_compliance/images/logo.svg"

add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/zw_compliance/images/logo.svg",
		"title": app_title,
		"route": "/app/zimra-fiscalisation",
		"has_permission": "zw_compliance.permissions.check_app_permission",
	}
]

# Installation
# ------------

after_install = "zw_compliance.fiscalisation.setup.install.after_install"
after_migrate = "zw_compliance.fiscalisation.setup.install.after_migrate"
before_uninstall = "zw_compliance.fiscalisation.setup.install.before_uninstall"

# Testing
# -------

before_tests = "zw_compliance.testing.before_tests"

# Document Events
# ---------------

_fiscal_invoice_events = {
	"before_insert": "zw_compliance.fiscalisation.guards.clear_fiscal_fields",
	"validate": "zw_compliance.fiscalisation.validation.validate_invoice",
	"before_submit": "zw_compliance.fiscalisation.validation.before_submit",
	"on_submit": "zw_compliance.fiscalisation.outbox.fiscalise",
	"before_cancel": "zw_compliance.fiscalisation.guards.prevent_cancel",
	"on_cancel": "zw_compliance.fiscalisation.guards.prevent_cancel",
	"on_trash": "zw_compliance.fiscalisation.guards.prevent_delete",
}

doc_events = {
	"Sales Invoice": _fiscal_invoice_events,
	"POS Invoice": _fiscal_invoice_events,
}

# Desk
# ----

doctype_js = {
	"Sales Invoice": "public/js/zimra_invoice.js",
	"POS Invoice": "public/js/zimra_invoice.js",
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"* * * * *": ["zw_compliance.fiscalisation.schedule.every_minute"],
		"*/5 * * * *": ["zw_compliance.fiscalisation.schedule.every_five_minutes"],
	},
	"daily": ["zw_compliance.fiscalisation.schedule.daily"],
}

# Jinja (print formats)
# ---------------------

jinja = {
	"methods": [
		"zw_compliance.fiscalisation.print_utils.zimra_fiscal_receipt",
		"zw_compliance.fiscalisation.print_utils.zimra_qr_svg",
		"zw_compliance.fiscalisation.print_utils.zimra_z_report",
	],
}
