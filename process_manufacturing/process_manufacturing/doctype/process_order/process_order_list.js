// Copyright (c) 2017, earthians and contributors
// For license information, please see license.txt

frappe.listview_settings['Process Order'] = {
	add_fields: ["status"],
	filters: [["status", "!=", "Cancelled"]],
	get_indicator: function(doc) {
		if(doc.status==="Submitted") {
			return [__("Not Started"), "orange", "status,=,Submitted"];
		} else {
			return [__(doc.status), {
				"Draft": "orange",
				"Scheduled": "darkgrey",
				"Completed": "green",
				"Cancelled": "red"
			}[doc.status], "status,=," + doc.status];
		}
	}
};
