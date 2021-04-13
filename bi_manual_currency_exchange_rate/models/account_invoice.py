# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models,api,_
from odoo.exceptions import UserError

		
class account_invoice(models.Model):
	_inherit ='account.move'
	
	manual_currency_rate_active = fields.Boolean('Apply Manual Exchange',copy=False)
	manual_currency_rate = fields.Float('Rate', digits=(12, 6),copy=False)
	updated_from_purchase = fields.Boolean('Updated From Purchase',copy=False)
 

class account_invoice_line(models.Model):
	_inherit ='account.move.line'
	
	manual_currency_rate = fields.Float('Rate', digits=(12, 6),copy=False)
	is_manual_rate_appllied = fields.Boolean("Is Manual Rate Appllied")

	@api.model
	def _get_fields_onchange_subtotal_model(self, price_subtotal, move_type, currency, company, date):
		''' This method is used to recompute the values of 'amount_currency', 'debit', 'credit' due to a change made
		in some business fields (affecting the 'price_subtotal' field).

		:param price_subtotal:  The untaxed amount.
		:param move_type:       The type of the move.
		:param currency:        The line's currency.
		:param company:         The move's company.
		:param date:            The move's date.
		:return:                A dictionary containing 'debit', 'credit', 'amount_currency'.
		'''
		if move_type in self.move_id.get_outbound_types():
			sign = 1
		elif move_type in self.move_id.get_inbound_types():
			sign = -1
		else:
			sign = 1

		amount_currency = price_subtotal * sign

		if not self.move_id.updated_from_purchase and  self.move_id.manual_currency_rate_active and self.move_id.manual_currency_rate != 0 and self.is_manual_rate_appllied == False :
			balance = amount_currency * self.move_id.manual_currency_rate
			self.is_manual_rate_appllied = True
		else:
			balance = currency._convert(amount_currency, company.currency_id, company, date or fields.Date.context_today(self))

		return {
			'amount_currency': amount_currency,
			'currency_id': currency.id,
			'debit': balance > 0.0 and balance or 0.0,
			'credit': balance < 0.0 and -balance or 0.0,
		}
	

	@api.onchange('product_id')
	def _onchange_product_id(self):
		for line in self:
			if not line.product_id or line.display_type in ('line_section', 'line_note'):
				continue

			line.name = line._get_computed_name()
			line.account_id = line._get_computed_account()
			line.tax_ids = line._get_computed_taxes()
			line.product_uom_id = line._get_computed_uom()
			line.price_unit = line._get_computed_price_unit()

			# price_unit and taxes may need to be adapted following Fiscal Position
			line._set_price_and_tax_after_fpos()

			# Convert the unit price to the invoice's currency.
			company = line.move_id.company_id
			line.price_unit = company.currency_id._convert(line.price_unit, line.move_id.currency_id, company, line.move_id.date, round=False)
			line.is_manual_rate_appllied = False


class stock_move(models.Model):
	_inherit = 'stock.move'

	def _create_in_svl(self, forced_quantity=None):
		"""Create a `stock.valuation.layer` from `self`.

		:param forced_quantity: under some circunstances, the quantity to value is different than
			the initial demand of the move (Default value = None)
		"""
		rec  = super(stock_move, self)._create_in_svl(forced_quantity=None)
		for rc in rec:
			for line in self:
				if line.purchase_line_id :
					if line.purchase_line_id.order_id.purchase_manual_currency_rate_active:
						price_unit = line.purchase_line_id.order_id.currency_id.round((line.purchase_line_id.price_unit)/line.purchase_line_id.order_id.purchase_manual_currency_rate)

						rc.write({'unit_cost' : price_unit,'value' :price_unit * rc.quantity,'remaining_value' : price_unit * rc.quantity})
		return rec

	def _prepare_account_move_line(self, qty, cost, credit_account_id, debit_account_id, description):
		"""
		Generate the account.move.line values to post to track the stock valuation difference due to the
		processing of the given quant.
		"""
		self.ensure_one()

		# the standard_price of the product may be in another decimal precision, or not compatible with the coinage of
		# the company currency... so we need to use round() before creating the accounting entries.
		debit_value = self.company_id.currency_id.round(cost)
		credit_value = debit_value

		valuation_partner_id = self._get_partner_id_for_valuation_lines()

		if self.purchase_line_id.order_id.purchase_manual_currency_rate_active:
			debit_value = self.purchase_line_id.order_id.currency_id.round((self.purchase_line_id.price_unit*qty)/self.purchase_line_id.order_id.purchase_manual_currency_rate)
			credit_value = debit_value
			
			res = [(0, 0, line_vals) for line_vals in self._generate_valuation_lines_data(valuation_partner_id, qty, debit_value, credit_value, debit_account_id, credit_account_id, description).values()]

		else:      
			res = [(0, 0, line_vals) for line_vals in self._generate_valuation_lines_data(valuation_partner_id, qty, debit_value, credit_value, debit_account_id, credit_account_id, description).values()]

		if self.sale_line_id.order_id.sale_manual_currency_rate:
			debit_value = self.sale_line_id.order_id.currency_id.round((self.sale_line_id.price_unit*qty)/self.sale_line_id.order_id.sale_manual_currency_rate)
			credit_value = debit_value
			res = [(0, 0, line_vals) for line_vals in self._generate_valuation_lines_data(valuation_partner_id, qty, debit_value, credit_value, debit_account_id, credit_account_id, description).values()]

		else:      
			res = [(0, 0, line_vals) for line_vals in self._generate_valuation_lines_data(valuation_partner_id, qty, debit_value, credit_value, debit_account_id, credit_account_id, description).values()]

		return res


	def _generate_valuation_lines_data(self, partner_id, qty, debit_value, credit_value, debit_account_id, credit_account_id, description):
		# This method returns a dictionary to provide an easy extension hook to modify the valuation lines (see purchase for an example)
		
		company_currency = self.company_id.currency_id
		
		diff_currency_po = self.purchase_line_id.order_id.currency_id != company_currency
		diff_currency_so = self.sale_line_id.order_id.currency_id != company_currency
		
		ctx = dict(self._context, lang=self.purchase_line_id.order_id.partner_id.lang)
		self.ensure_one()
		if self._context.get('forced_ref'):
			ref = self._context['forced_ref']
		else:
			ref = self.picking_id.name
		if self.purchase_line_id:
			debit_line_vals = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'debit': debit_value if debit_value > 0 else 0,
				'credit': -debit_value if debit_value < 0 else 0,
				'account_id': debit_account_id,
				'amount_currency': diff_currency_po and (self.purchase_line_id.price_unit)*qty,
				'currency_id': diff_currency_po and self.purchase_line_id.order_id.currency_id.id,
			}

			credit_line_vals = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'credit': credit_value if credit_value > 0 else 0,
				'debit': -credit_value if credit_value < 0 else 0,
				'account_id': credit_account_id,
				'amount_currency': diff_currency_po and (-self.purchase_line_id.price_unit)*qty,
				'currency_id': diff_currency_po and self.purchase_line_id.order_id.currency_id.id,
			}
		elif self.sale_line_id and self.sale_line_id.order_id.sale_manual_currency_rate_active:
			debit_line_vals = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'debit': debit_value if debit_value > 0 else 0,
				'credit': -debit_value if debit_value < 0 else 0,
				'account_id': debit_account_id,
				'amount_currency': diff_currency_so and (self.sale_line_id.price_unit)*qty,
				'currency_id': diff_currency_so and self.sale_line_id.order_id.currency_id.id,
			}

			credit_line_vals = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'credit': credit_value if credit_value > 0 else 0,
				'debit': -credit_value if credit_value < 0 else 0,
				'account_id': credit_account_id,
				'amount_currency': diff_currency_so and (-self.sale_line_id.price_unit)*qty,
				'currency_id': diff_currency_so and self.sale_line_id.order_id.currency_id.id,
			}
		else:
			debit_line_vals = {
					'name': self.name,
					'product_id': self.product_id.id,
					'quantity': qty,
					'product_uom_id': self.product_id.uom_id.id,
					'ref': ref,
					'partner_id': partner_id,
					'debit': debit_value if debit_value > 0 else 0,
					'credit': -debit_value if debit_value < 0 else 0,
					'account_id': debit_account_id,
				}

			credit_line_vals = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'credit': credit_value if credit_value > 0 else 0,
				'debit': -credit_value if credit_value < 0 else 0,
				'account_id': credit_account_id,
			}

		rslt = {'credit_line_vals': credit_line_vals, 'debit_line_vals': debit_line_vals}
		if credit_value != debit_value:
			# for supplier returns of product in average costing method, in anglo saxon mode
			diff_amount = debit_value - credit_value
			price_diff_account = self.product_id.property_account_creditor_price_difference

			if not price_diff_account:
				price_diff_account = self.product_id.categ_id.property_account_creditor_price_difference_categ
			if not price_diff_account:
				raise UserError(_('Configuration error. Please configure the price difference account on the product or its category to process this operation.'))

			rslt['price_diff_line_vals'] = {
				'name': self.name,
				'product_id': self.product_id.id,
				'quantity': qty,
				'product_uom_id': self.product_id.uom_id.id,
				'ref': ref,
				'partner_id': partner_id,
				'credit': diff_amount > 0 and diff_amount or 0,
				'debit': diff_amount < 0 and -diff_amount or 0,
				'account_id': price_diff_account.id,
			}
		return rslt

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
