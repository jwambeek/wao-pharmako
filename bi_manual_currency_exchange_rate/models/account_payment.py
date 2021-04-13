# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models,api, _


class account_payment(models.TransientModel):
	_inherit ='account.payment.register'

	manual_currency_rate_active = fields.Boolean('Apply Manual Exchange')
	manual_currency_rate = fields.Float('Rate', digits=(12, 6))

	@api.model
	def default_get(self, default_fields):
		rec = super(account_payment, self).default_get(default_fields)
		active_ids = self._context.get('active_ids') or self._context.get('active_id')
		active_model = self._context.get('active_model')

		# Check for selected invoices ids
		if not active_ids or active_model != 'account.move':
			return rec
		invoices = self.env['account.move'].browse(active_ids).filtered(lambda move: move.is_invoice(include_receipts=True))
		for inv in invoices:
			crncy_rate_active = inv.manual_currency_rate_active
			crncy_rate = inv.manual_currency_rate

			if self.manual_currency_rate_active and self.manual_currency_rate :
				crncy_rate_active = self.manual_currency_rate_active
				crncy_rate = self.manual_currency_rate

			rec.update({
				'manual_currency_rate_active':crncy_rate_active,
				'manual_currency_rate':crncy_rate
			})
		return rec

	@api.depends('source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id', 'payment_date', 'manual_currency_rate')
	def _compute_amount(self):
		for wizard in self:
			if wizard.source_currency_id == wizard.currency_id:
				# Same currency.
				wizard.amount = wizard.source_amount_currency
			elif wizard.currency_id == wizard.company_id.currency_id:
				# Payment expressed on the company's currency.
				wizard.amount = wizard.source_amount
			else:
				# Foreign currency on payment different than the one set on the journal entries.
				amount_payment_currency = wizard.company_id.currency_id._convert(wizard.source_amount, wizard.currency_id, wizard.company_id, wizard.payment_date)
				wizard.amount = amount_payment_currency

	@api.depends('amount')
	def _compute_payment_difference(self):
		for wizard in self:
			if wizard.source_currency_id == wizard.currency_id:
				# Same currency.
				wizard.payment_difference = wizard.source_amount_currency - wizard.amount
			elif wizard.currency_id == wizard.company_id.currency_id:
				# Payment expressed on the company's currency.
				wizard.payment_difference = wizard.source_amount - wizard.amount
			else:
				# Foreign currency on payment different than the one set on the journal entries.
				amount_payment_currency = wizard.company_id.currency_id._convert(wizard.source_amount, wizard.currency_id, wizard.company_id, wizard.payment_date)
				wizard.payment_difference = amount_payment_currency - wizard.amount


	def _create_payment_vals_from_wizard(self):
		res = super(account_payment, self)._create_payment_vals_from_wizard()
		if self.manual_currency_rate_active:
			res.update({
				'manual_currency_rate_active': self.manual_currency_rate_active, 
				'manual_currency_rate': self.manual_currency_rate
			})
		return res

	def _create_payment_vals_from_batch(self,batch_result):
		res = super(account_payment, self)._create_payment_vals_from_batch(batch_result)
		if self.manual_currency_rate_active:
			res.update({
				'manual_currency_rate_active': self.manual_currency_rate_active, 
				'manual_currency_rate': self.manual_currency_rate
			})
		return res



class AccountPayment(models.Model):
	_inherit = "account.payment"
	_description = "Payments"


	manual_currency_rate_active = fields.Boolean('Apply Manual Exchange')
	manual_currency_rate = fields.Float('Rate', digits=(12, 6))

	def _prepare_move_line_default_vals(self, write_off_line_vals=None):
		''' Prepare the dictionary to create the default account.move.lines for the current payment.
		:param write_off_line_vals: Optional dictionary to create a write-off account.move.line easily containing:
			* amount:       The amount to be added to the counterpart amount.
			* name:         The label to set on the line.
			* account_id:   The account on which create the write-off.
		:return: A list of python dictionary to be passed to the account.move.line's 'create' method.
		'''
		self.ensure_one()
		write_off_line_vals = write_off_line_vals or {}

		if not self.journal_id.payment_debit_account_id or not self.journal_id.payment_credit_account_id:
			raise UserError(_(
				"You can't create a new payment without an outstanding payments/receipts accounts set on the %s journal."
			) % self.journal_id.display_name)

		# Compute amounts.
		write_off_amount = write_off_line_vals.get('amount', 0.0)

		if self.payment_type == 'inbound':
			# Receive money.
			counterpart_amount = -self.amount
			write_off_amount *= -1
		elif self.payment_type == 'outbound':
			# Send money.
			counterpart_amount = self.amount
		else:
			counterpart_amount = 0.0
			write_off_amount = 0.0

		if self.manual_currency_rate_active:
			balance = counterpart_amount * self.manual_currency_rate
			counterpart_amount_currency = counterpart_amount
			write_off_balance = write_off_amount * self.manual_currency_rate
			write_off_amount_currency = write_off_amount
			currency_id = self.currency_id.id
		
		else:
			balance = self.currency_id._convert(counterpart_amount, self.company_id.currency_id, self.company_id, self.date)
			counterpart_amount_currency = counterpart_amount
			write_off_balance = self.currency_id._convert(write_off_amount, self.company_id.currency_id, self.company_id, self.date)
			write_off_amount_currency = write_off_amount
			currency_id = self.currency_id.id

		if self.is_internal_transfer:
			if self.payment_type == 'inbound':
				liquidity_line_name = _('Transfer to %s', self.journal_id.name)
			else: # payment.payment_type == 'outbound':
				liquidity_line_name = _('Transfer from %s', self.journal_id.name)
		else:
			liquidity_line_name = self.payment_reference

		# Compute a default label to set on the journal items.

		payment_display_name = {
			'outbound-customer': _("Customer Reimbursement"),
			'inbound-customer': _("Customer Payment"),
			'outbound-supplier': _("Vendor Payment"),
			'inbound-supplier': _("Vendor Reimbursement"),
		}

		default_line_name = self.env['account.move.line']._get_default_line_name(
			payment_display_name['%s-%s' % (self.payment_type, self.partner_type)],
			self.amount,
			self.currency_id,
			self.date,
			partner=self.partner_id,
		)

		line_vals_list = [
			# Liquidity line.
			{
				'name': liquidity_line_name or default_line_name,
				'date_maturity': self.date,
				'amount_currency': -counterpart_amount_currency,
				'currency_id': currency_id,
				'debit': balance < 0.0 and -balance or 0.0,
				'credit': balance > 0.0 and balance or 0.0,
				'partner_id': self.partner_id.id,
				'account_id': self.journal_id.payment_debit_account_id.id if balance < 0.0 else self.journal_id.payment_credit_account_id.id,
			},
			# Receivable / Payable.
			{
				'name': self.payment_reference or default_line_name,
				'date_maturity': self.date,
				'amount_currency': counterpart_amount_currency + write_off_amount_currency if currency_id else 0.0,
				'currency_id': currency_id,
				'debit': balance + write_off_balance > 0.0 and balance + write_off_balance or 0.0,
				'credit': balance + write_off_balance < 0.0 and -balance - write_off_balance or 0.0,
				'partner_id': self.partner_id.id,
				'account_id': self.destination_account_id.id,
			},
		]
		if write_off_balance:
			# Write-off line.
			line_vals_list.append({
				'name': write_off_line_vals.get('name') or default_line_name,
				'amount_currency': -write_off_amount_currency,
				'currency_id': currency_id,
				'debit': write_off_balance < 0.0 and -write_off_balance or 0.0,
				'credit': write_off_balance > 0.0 and write_off_balance or 0.0,
				'partner_id': self.partner_id.id,
				'account_id': write_off_line_vals.get('account_id'),
			})

		return line_vals_list


class AccountReconciliationInherit(models.AbstractModel):
	_inherit = 'account.reconciliation.widget'

	####################################################
	# Public
	####################################################

	@api.model
	def process_bank_statement_line(self, st_line_ids, data):
		""" Handles data sent from the bank statement reconciliation widget
			(and can otherwise serve as an old-API bridge)

			:param st_line_ids
			:param list of dicts data: must contains the keys
				'counterpart_aml_dicts', 'payment_aml_ids' and 'new_aml_dicts',
				whose value is the same as described in process_reconciliation
				except that ids are used instead of recordsets.
			:returns dict: used as a hook to add additional keys.
		"""
		st_lines = self.env['account.bank.statement.line'].browse(st_line_ids)
		ctx = dict(self._context, force_price_include=False)

		for st_line, datum in zip(st_lines, data):
			if datum.get('partner_id') is not None:
				st_line.write({'partner_id': datum['partner_id'],'manual_currency_rate':datum.get('manual_currency_rate')})
			st_line.with_context(ctx).reconcile(datum.get('lines_vals_list', []), to_check=datum.get('to_check', False))
		return {'statement_line_ids': st_lines}


class AccountBankStatementLineInherit(models.Model):
	_inherit = "account.bank.statement.line"

	@api.model
	def _prepare_liquidity_move_line_vals(self):
		''' Prepare values to create a new account.move.line record corresponding to the
		liquidity line (having the bank/cash account).
		:return:        The values to create a new account.move.line record.
		'''
		self.ensure_one()

		statement = self.statement_id
		journal = statement.journal_id
		company_currency = journal.company_id.currency_id
		journal_currency = journal.currency_id if journal.currency_id != company_currency else False

		if self.foreign_currency_id and journal_currency:
			currency_id = journal_currency.id
			if self.foreign_currency_id == company_currency:
				amount_currency = self.amount
				if self.manual_currency_rate :
					balance = self.amount *self.manual_currency_rate
				else:
					balance = self.amount_currency
			else:
				amount_currency = self.amount
				if self.manual_currency_rate :
					balance = self.amount *self.manual_currency_rate
				else:
					balance = journal_currency._convert(amount_currency, company_currency, journal.company_id, self.date)
		elif self.foreign_currency_id and not journal_currency:
			amount_currency = self.amount_currency
			if self.manual_currency_rate :
				balance = self.amount *self.manual_currency_rate
			else:
				balance = self.amount
			currency_id = self.foreign_currency_id.id
		elif not self.foreign_currency_id and journal_currency:
			currency_id = journal_currency.id
			amount_currency = self.amount
			if self.manual_currency_rate :
				balance = self.amount *self.manual_currency_rate
			else:
				balance = journal_currency._convert(amount_currency, journal.company_id.currency_id, journal.company_id, self.date)
		else:
			currency_id = company_currency.id
			amount_currency = self.amount
			if self.manual_currency_rate :
				balance = self.amount *self.manual_currency_rate
			else:
				balance = self.amount
		return {
			'name': self.payment_ref,
			'move_id': self.move_id.id,
			'partner_id': self.partner_id.id,
			'currency_id': currency_id,
			'account_id': journal.default_account_id.id,
			'debit': balance > 0 and balance or 0.0,
			'credit': balance < 0 and -balance or 0.0,
			'amount_currency': amount_currency,
		}

	@api.model
	def _prepare_counterpart_move_line_vals(self, counterpart_vals, move_line=None):
		''' Prepare values to create a new account.move.line move_line.
		By default, without specified 'counterpart_vals' or 'move_line', the counterpart line is
		created using the suspense account. Otherwise, this method is also called during the
		reconciliation to prepare the statement line's journal entry. In that case,
		'counterpart_vals' will be used to create a custom account.move.line (from the reconciliation widget)
		and 'move_line' will be used to create the counterpart of an existing account.move.line to which
		the newly created journal item will be reconciled.
		:param counterpart_vals:    A python dictionary containing:
			'balance':                  Optional amount to consider during the reconciliation. If a foreign currency is set on the
										counterpart line in the same foreign currency as the statement line, then this amount is
										considered as the amount in foreign currency. If not specified, the full balance is took.
										This value must be provided if move_line is not.
			'amount_residual':          The residual amount to reconcile expressed in the company's currency.
										/!\ This value should be equivalent to move_line.amount_residual except we want
										to avoid browsing the record when the only thing we need in an overview of the
										reconciliation, for example in the reconciliation widget.
			'amount_residual_currency': The residual amount to reconcile expressed in the foreign's currency.
										Using this key doesn't make sense without passing 'currency_id' in vals.
										/!\ This value should be equivalent to move_line.amount_residual_currency except
										we want to avoid browsing the record when the only thing we need in an overview
										of the reconciliation, for example in the reconciliation widget.
			**kwargs:                   Additional values that need to land on the account.move.line to create.
		:param move_line:           An optional account.move.line move_line representing the counterpart line to reconcile.
		:return:                    The values to create a new account.move.line move_line.
		'''
		self.ensure_one()

		statement = self.statement_id
		journal = statement.journal_id
		company_currency = journal.company_id.currency_id
		journal_currency = journal.currency_id or company_currency
		foreign_currency = self.foreign_currency_id or journal_currency or company_currency
		statement_line_rate = (self.amount_currency / self.amount) if self.amount else 0.0
		balance_to_reconcile = counterpart_vals.pop('balance', None)
		amount_residual = -counterpart_vals.pop('amount_residual', move_line.amount_residual if move_line else 0.0) \
			if balance_to_reconcile is None else balance_to_reconcile
		amount_residual_currency = -counterpart_vals.pop('amount_residual_currency', move_line.amount_residual_currency if move_line else 0.0)\
			if balance_to_reconcile is None else balance_to_reconcile

		if 'currency_id' in counterpart_vals:
			currency_id = counterpart_vals['currency_id'] or company_currency.id
		elif move_line:
			currency_id = move_line.currency_id.id or company_currency.id
		else:
			currency_id = foreign_currency.id

		if currency_id not in (foreign_currency.id, journal_currency.id):
			currency_id = company_currency.id
			amount_residual_currency = 0.0

		amounts = {
			company_currency.id: 0.0,
			journal_currency.id: 0.0,
			foreign_currency.id: 0.0,
		}

		amounts[currency_id] = amount_residual_currency
		amounts[company_currency.id] = amount_residual

		if currency_id == journal_currency.id and journal_currency != company_currency:
			if foreign_currency != company_currency:
				amounts[company_currency.id] = journal_currency._convert(amounts[currency_id], company_currency, journal.company_id, self.date)
			if statement_line_rate:
				amounts[foreign_currency.id] = amounts[currency_id] * statement_line_rate
		elif currency_id == foreign_currency.id and self.foreign_currency_id:
			if statement_line_rate:
				amounts[journal_currency.id] = amounts[foreign_currency.id] / statement_line_rate
				if foreign_currency != company_currency:
					amounts[company_currency.id] = journal_currency._convert(amounts[journal_currency.id], company_currency, journal.company_id, self.date)
		else:
			amounts[journal_currency.id] = company_currency._convert(amounts[company_currency.id], journal_currency, journal.company_id, self.date)
			if statement_line_rate:
				amounts[foreign_currency.id] = amounts[journal_currency.id] * statement_line_rate

		if foreign_currency == company_currency and journal_currency != company_currency and self.foreign_currency_id:
			if self.manual_currency_rate:
				balance = -(self.amount * self.manual_currency_rate)
			else:
				balance = amounts[foreign_currency.id]
		else:
			if self.manual_currency_rate:
				balance = -(self.amount * self.manual_currency_rate)
			else:
				balance = amounts[company_currency.id]

		if foreign_currency != company_currency and self.foreign_currency_id:
			amount_currency = amounts[foreign_currency.id]
			currency_id = foreign_currency.id
		elif journal_currency != company_currency and not self.foreign_currency_id:
			amount_currency = amounts[journal_currency.id]
			currency_id = journal_currency.id
		else:
			amount_currency = amounts[company_currency.id]
			currency_id = company_currency.id
		return {
			**counterpart_vals,
			'name': counterpart_vals.get('name', move_line.name if move_line else ''),
			'move_id': self.move_id.id,
			'partner_id': self.partner_id.id or (move_line.partner_id.id if move_line else False),
			'currency_id': currency_id,
			'account_id': counterpart_vals.get('account_id', move_line.account_id.id if move_line else False),
			'debit': balance if balance > 0.0 else 0.0,
			'credit': -balance if balance < 0.0 else 0.0,
			'amount_currency': amount_currency,
		}
	
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
