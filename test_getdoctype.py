import frappe

frappe.set_user("Administrator")
from frappe.desk.form.load import getdoctype

try:
    getdoctype("Bulk Overtime", with_parent=1)
    docs = frappe.response.get("docs", [])
    print(f"getdoctype returned {len(docs)} docs")
    for d in docs:
        if hasattr(d, 'doctype') and d.doctype == "DocType":
            print(f"  {d.name}: {len(d.fields)} fields")
    print("SUCCESS - getdoctype works fine")
except Exception as e:
    print(f"getdoctype FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
