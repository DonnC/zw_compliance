import frappe


def log_and_throw(self, message, title="Zimra App Error", throw:bool=False):
    """Logs error in Frappe and may throw an exception."""
    frappe.log_error(title=title, message=message)

    if throw:
        frappe.throw(message)