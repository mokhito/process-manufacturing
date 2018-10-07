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
		if not self.fg_warehouse:
			frappe.throw(_("Target Warehouse is required before Submit"))
		frappe.db.set(self, 'status', 'Submitted')

	def on_cancel(self):
		stock_entry = frappe.db.sql("""select name from `tabStock Entry`
			where process_order = %s and docstatus = 1""", self.name)
		if stock_entry:
			frappe.throw(_("Cannot cancel because submitted Stock Entry \
			{0} exists").format(stock_entry[0][0]))
		frappe.db.set(self, 'status', 'Cancelled')

	def get_process_details(self):
		#	Set Child Tables
		process = frappe.get_doc("Process Definition", self.process_name)
		if process:
			if process.materials:
				self.add_item_in_table(process.materials, "materials")
			if process.finished_products:
				self.add_item_in_table(process.finished_products, "finished_products")

	def start_finish_processing(self, status):
		if status == "Scheduled":
			if not self.end_dt:
				self.end_dt = get_datetime()
			self.flags.ignore_validate_update_after_submit = True
			self.save()
			return self.make_stock_entry(status)
		return None

	def set_se_items_finish(self, se):
		#set from and to warehouse
		se.from_warehouse = self.src_warehouse
		se.to_warehouse = self.fg_warehouse

		se_materials = frappe.get_doc("Stock Entry",{"process_order": self.name, "docstatus": '1'})
		#get items to consume from previous stock entry or append to items
		#TODO allow multiple raw material transfer
		raw_material_cost = 0
		operating_cost = 0
		if se_materials:
			raw_material_cost = se_materials.total_incoming_value
			se.items = se_materials.items
			for item in se.items:
				item.s_warehouse = se.from_warehouse
				item.t_warehouse = None
		else:
			for item in self.materials:
				se = self.set_se_items(se, item, se.from_warehouse, None, False)
				#TODO calc raw_material_cost

		production_cost = raw_material_cost

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

		#add Stock Entry Items for produced goods
		for item in self.finished_products:
			se = self.set_se_items(se, item, None, se.to_warehouse, True, qty_of_total_production, total_sale_value, production_cost)

		return se

	def set_se_items(self, se, item, s_wh, t_wh, calc_basic_rate=False, qty_of_total_production=None, total_sale_value=None, production_cost=None):
		if item.quantity > 0:
			item_name, stock_uom, description, item_expense_account, item_cost_center = frappe.db.get_values("Item", item.item, \
			["item_name", "stock_uom", "description", "expense_account", "buying_cost_center"])[0]

			se_item = se.append("items")
			se_item.item_code = item.item
			se_item.qty = item.quantity
			se_item.s_warehouse = s_wh
			se_item.t_warehouse = t_wh
			se_item.item_name = item_name
			se_item.description = description
			se_item.uom = stock_uom
			se_item.stock_uom = stock_uom

			se_item.expense_account = item_expense_account
			se_item.cost_center = item_cost_center

			# in stock uom
			se_item.transfer_qty = item.quantity
			se_item.conversion_factor = 1.00

			item_details = se.run_method( "get_item_details",args = (frappe._dict(
			{"item_code": item.item, "company": self.company, "uom": stock_uom, 's_warehouse': s_wh})), for_update=True)

			for f in ("uom", "stock_uom", "description", "item_name", "expense_account",
			"cost_center", "conversion_factor"):
				se_item.set(f, item_details.get(f))
				
				se_item.basic_rate = production_cost/qty_of_total_production
		return se

	def make_stock_entry(self, status):
		stock_entry = frappe.new_doc("Stock Entry")
		stock_entry.process_order = self.name
		if status == "Scheduled":
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
	if po.status == "Draft":
		po.status = "Scheduled"
	if po.status == "Scheduled":
		po.status = "Completed"
	elif po.status in ["Completed", "Cancelled"]:
		frappe.throw("You cannot make entries against Completed/Cancelled Process Orders")
	po.flags.ignore_validate_update_after_submit = True
	po.save()

def manage_se_cancel(se, po):
	if(po.status == "Completed"):
		try:
			validate_material_qty(se.items, po.finished_products)
			po.status = "Scheduled"
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
		validate_material_qty(se.items, po.scrap)

@frappe.whitelist()
def manage_se_changes(doc, method):
	if doc.process_order:
		po = frappe.get_doc("Process Order", doc.process_order)
		if(method=="on_submit"):
			if po.status == "Scheduled":
				po_items = po.materials
				po_items.extend(po.finished_products)
				po_items.extend(po.scrap)
				validate_items(doc.items, po_items)
			validate_se_qty(doc, po)
			manage_se_submit(doc, po)
		elif(method=="on_cancel"):
			manage_se_cancel(doc, po)
