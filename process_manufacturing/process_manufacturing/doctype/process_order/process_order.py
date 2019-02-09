# -*- coding: utf-8 -*-
# Copyright (c) 2018, earthians and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, time_diff_in_hours
from frappe import _

class ProcessOrder(Document):
    def on_submit(self):
        frappe.db.set(self, 'status', 'Submitted')

    def on_cancel(self):
        stock_entry = frappe.db.sql("""select name from `tabStock Entry`
            where process_order = %s and docstatus = 1""", self.name)
        if stock_entry:
            frappe.throw(_("Cannot cancel because submitted Stock Entry \
            {0} exists").format(stock_entry[0][0]))
        frappe.db.set(self, 'status', 'Cancelled')

    def get_process_details(self):
        # Set Child Tables
        process = frappe.get_doc("Process Definition", self.process_name)
        if process:
            if process.materials:
                self.add_item_in_table(process.materials, "materials")
            if process.finished_products:
                self.add_item_in_table(process.finished_products, "finished_products")

    def start_finish_processing(self, status):
        self.end_dt = get_datetime()
        self.flags.ignore_validate_update_after_submit = True
        self.save()
        return self.make_stock_entry(status)

    def set_se_items_finish(self, se):
        # Add materials to Stock Entry
        for item in self.materials:
            se = self.set_se_items(se, item)

        production_cost = 0

        #calc total_qty and total_sale_value
        qty_of_total_production = 0
        total_sale_value = 0
        for item in self.finished_products:
            if item.quantity > 0:
                qty_of_total_production = float(qty_of_total_production) + item.quantity
                sale_value_of_pdt = frappe.db.get_value("Item Price", {"item_code":item.item}, "price_list_rate")

                if sale_value_of_pdt:
                    total_sale_value += float(sale_value_of_pdt) * item.quantity
                else:
                    frappe.throw(_("Selling price not set for item {0}").format(item.item))

        # Add produced items to Stock Entry
        for item in self.finished_products:
            se = self.set_se_items(se, item, False)

        return se

    def set_se_items(self, se, item, materials=True):
        if item.quantity > 0:
            item_name, stock_uom, description, item_expense_account, item_cost_center = frappe.db.get_values("Item", item.item, \
            ["item_name", "stock_uom", "description", "expense_account", "buying_cost_center"])[0]

            se_item = se.append("items")
            se_item.item_code = item.item
            se_item.qty = item.quantity
            se_item.item_name = item_name
            se_item.description = description
            se_item.uom = stock_uom
            se_item.stock_uom = stock_uom

            se_item.expense_account = item_expense_account
            se_item.cost_center = item_cost_center

            # in stock uom
            se_item.transfer_qty = item.quantity
            se_item.conversion_factor = 1.00

            item_details = se.run_method("get_item_details", args = (frappe._dict(
            {"item_code": item.item, "company": self.company, "uom": stock_uom})), for_update=True)

            item_defaults = frappe.get_doc("Item", item.item)

            for f in ("uom", "stock_uom", "description", "item_name", "expense_account",
            "cost_center", "conversion_factor"):
                se_item.set(f, item_details.get(f))
                se_item.basic_rate = 0

            if materials:
                se_item.set("s_warehouse", item_defaults.get("default_warehouse"))
                se_item.set("t_warehouse", None)
            else:
                se_item.set("s_warehouse", None)
                se_item.set("t_warehouse", item_defaults.get("default_warehouse"))

                # create batch id if needed
                if item_details.get("has_batch_no") and item_details.get("create_new_batch"):
                    shelf_life = item_details.get("shelf_life") or 0
                    prod_date = frappe.utils.nowdate()
                    exp_date = frappe.utils.add_days(prod_date, shelf_life)
                    batch = frappe.get_doc({
                        "doctype": "Batch",
                        "item": item.item,
                        "manufacturing_date": prod_date,
                        "expiry_date": exp_date,
                        "naming_series": item_details.get("batch_naming_series")
                    })
                    batch.autoname()
                    batch.insert(ignore_permissions=True)
                    se_item.set("batch_no", batch.name)

        return se

    def make_stock_entry(self, status):
        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.process_order = self.name
        stock_entry.purpose = "Manufacture"
        stock_entry = self.set_se_items_finish(stock_entry)

        return stock_entry.as_dict()

    def add_item_in_table(self, table_value, table_name):
        self.set(table_name, [])
        for item in table_value:
            po_item = self.append(table_name, {})
            po_item.item = item.item
            po_item.item_name = item.item_name

def validate_items(se_items, po_items):
    #validate for items not in process order
    for se_item in se_items:
        if not filter(lambda x: x.item == se_item.item_code, po_items):
            frappe.throw(_("Item {0} - {1} cannot be part of this Stock Entry").format(se_item.item_code, se_item.item_name))

def validate_material_qty(se_items, po_items):
    #TODO allow multiple raw material transfer?
    for material in po_items:
        qty = 0
        for item in se_items:
            if(material.item == item.item_code):
                qty += item.qty
        if(qty != material.quantity):
            frappe.throw(_("Total quantity of Item {0} - {1} should be {2}"\
            ).format(material.item, material.item, material.quantity))

def manage_se_submit(se, po):
    if po.docstatus == 0:
        frappe.throw(_("Submit the  Process Order {0} to make Stock Entries").format(po.name))
    if po.status == "Submitted":
        po.status = "Completed"
        po.start_dt = get_datetime()
    elif po.status in ["Completed", "Cancelled"]:
        frappe.throw("You cannot make entries against Completed/Cancelled Process Orders")
    po.flags.ignore_validate_update_after_submit = True
    po.save()

def manage_se_cancel(se, po):
    if(po.status == "Completed"):
        try:
            validate_material_qty(se.items, po.finished_products)
            po.status = "Submitted"
        except:
            frappe.throw("Please cancel the production stock entry first.")
    else:
        frappe.throw("Process order status must be completed.")
    po.flags.ignore_validate_update_after_submit = True
    po.save()

def validate_se_qty(se, po):
    validate_material_qty(se.items, po.materials)
    if po.status == "Submitted":
        validate_material_qty(se.items, po.finished_products)

@frappe.whitelist()
def manage_se_changes(doc, method):
    if doc.process_order:
        po = frappe.get_doc("Process Order", doc.process_order)
        if(method=="on_submit"):
            if po.status == "Submitted":
                po_items = po.materials
                po_items.extend(po.finished_products)
                validate_items(doc.items, po_items)
            validate_se_qty(doc, po)
            manage_se_submit(doc, po)
        elif(method=="on_cancel"):
            manage_se_cancel(doc, po)
