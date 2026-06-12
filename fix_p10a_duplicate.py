import frappe

def execute():
    # Ensure both expected names exist so csf_ke and payroll_africa
    # each find their own row on fixture sync and update instead of insert
    
    payroll_name = "Salary Component-p10a_tax_deduction_card_type"
    csfke_name = "Salary Component-custom_p10a_tax_deduction_card_type"
    
    # If only the payroll_africa row exists, clone it for csf_ke
    if frappe.db.exists("Custom Field", payroll_name) and \
       not frappe.db.exists("Custom Field", csfke_name):
        
        frappe.db.sql("""
            INSERT INTO `tabCustom Field`
            (name, dt, fieldname, label, fieldtype, module, owner, creation, modified, modified_by)
            SELECT %s, dt, fieldname, label, fieldtype, 'CSF KE', owner, NOW(), modified, modified_by
            FROM `tabCustom Field`
            WHERE name = %s
        """, (csfke_name, payroll_name))
        
        frappe.db.commit()
