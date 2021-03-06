import datetime
import hashlib
import json
import mimetypes
import uuid

import aiohttp_jinja2
import jinja2

from aiohttp import web

from api.common import SESSION_IDS_TO_USER
from api.db import DataBase
from api.models import CardPayment, RequestedPayment
from jinja2 import Template

from api.utils import auth_required


class Api:
    def __init__(self):
        self.app = None
        self.db = None

    async def init_connection(self, app):
        self.db = DataBase()
        self.app = app
        aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('./front/'))
        await self.db.initialize()

    async def card_payment_post(self, request):
        post = await request.json()

        card_number = post.get('card_number')
        amount = int(post.get('amount'))
        card_ttl = datetime.datetime.strptime(post.get('card_ttl'), '%m.%Y').date()
        cvc = post.get('cvc')
        comment = post.get('comment')
        email = post.get('email')

        card_payment = CardPayment(
            card_number=card_number, amount=amount, card_ttl=card_ttl,
            cvc=cvc, comment=comment, email=email,
        )

        try:
            await self.db.create_card_payment(card_payment)
            return self._json_response({
                'result': 'OK',
            })
        except Exception:
            return self._error()

    async def card_payment_get(self, request):
        result = await self._get_card_payments(request)

        return self._json_response({
            'result': result,
        })

    async def requested_payment_post(self, request):
        post = await request.json()

        tax = post.get('tax')
        bic = post.get('bic')
        account_number = post.get('account_number')
        phone = post.get('phone')
        comment = post.get('comment')
        email = post.get('email')
        amount = int(post.get('amount'))

        card_payment = RequestedPayment(
            tax=tax, amount=amount, bic=bic, phone=phone,
            account_number=account_number, comment=comment, email=email,
        )

        try:
            await self.db.create_requested_payment(card_payment)
            return self._json_response({
                'result': 'OK',
            })
        except Exception:
            return self._error()

    async def requested_payment_get(self, request):
        result = await self._get_requested_payments(request)

        return self._json_response({
            'result': result,
        })

    async def card_payment_patch(self, request):
        post = await request.json()
        is_safe = not post.get('notSafe')
        payment_id = int(post.get('payment_id'))

        await self.db.patch_card_payment(payment_id, is_safe)

        return self._json_response({
            'result': 'OK',
        })

    @auth_required
    async def card_payments(self, request):
        card_payments = await self._get_card_payments(request)

        return aiohttp_jinja2.render_template(
            'card_payments.html', request, {'card_payments': card_payments}
        )

    @auth_required
    async def requested_payments(self, request):
        requested_payments = await self._get_requested_payments(request)

        return aiohttp_jinja2.render_template(
            'requested_payments.html', request, {'requested_payments': requested_payments}
        )

    @staticmethod
    async def internet_bank_payment(request):
        post = await request.json()
        with open('./server/api/templates/internet_bank_payment.template', 'r') as file_reader:
            template = Template(file_reader.read())

        result = template.render(
            payment_from=post.get('payment_from'),
            bic=post.get('bic'),
            account_number=post.get('account_number'),
            comment=post.get('comment'),
            amount=post.get('amount'),
        )
        result = result.encode()

        resp = web.StreamResponse(headers={
            'CONTENT-DISPOSITION': 'attachment; filename=internet_bank_payment.txt'
        })
        resp.content_type = mimetypes.guess_type('internet_bank_payment.txt')
        resp.content_length = len(result)
        await resp.prepare(request)
        await resp.write(result)

        return resp

    @staticmethod
    async def payment_customer(request):
        customer = request.match_info.get('customer', None)
        if not customer:
            return web.HTTPBadRequest()

        with open(f'./customers/{customer}.json', 'r') as file_reader:
            customer_info = json.load(file_reader)

        with open(f'./front/customers_products/{customer}/info.json', 'r') as file_reader:
            products_info = json.load(file_reader)

        return aiohttp_jinja2.render_template(
            'payment.html',
            request,
            {
                'customer': customer,
                'customer_info': customer_info,
                'products_info': products_info,
            },
        )

    @staticmethod
    async def logout(request):
        if not request.user:
            return web.HTTPPermanentRedirect('/admin')

        cookies = request.cookies
        sid = cookies.get('sid')
        SESSION_IDS_TO_USER.pop(sid)

        return web.HTTPTemporaryRedirect('/admin')

    @staticmethod
    async def admin(request):
        return aiohttp_jinja2.render_template(
            'admin.html', request, {'user': request.user}
        )

    async def admin_post(self, request):
        post = await request.post()

        login = post.get('login')
        password = post.get('password')

        if not login or not password:
            error = 'Не хватает логина или пароля'
            return aiohttp_jinja2.render_template('admin.html', request, {'error': error})

        sha256_password = hashlib.sha256(password.encode()).hexdigest()
        user = await self.db.get_user(login, sha256_password)

        if user:
            sid = uuid.uuid4()
            SESSION_IDS_TO_USER[str(sid)] = user

            response = aiohttp_jinja2.render_template('admin.html', request, {'user': user})
            response.set_cookie(
                'sid',
                sid,
                expires=datetime.datetime.now() + datetime.timedelta(hours=24),
            )

            return response
        else:
            error = 'Неверный логин или пароль!'
            return aiohttp_jinja2.render_template('admin.html', request, {'error': error})

    @staticmethod
    def _json_response(message):
        return web.json_response(
            message,
            headers={
                'Access-Control-Allow-Origin': '*',
            }
        )

    async def _get_card_payments(self, request):
        sort_by, sort_order, filter_by, filter_value = self._get_filter_query(request)

        result = []
        card_payments = await self.db.get_card_payments(filter_by, filter_value, sort_by, sort_order)

        for row in await card_payments:
            result.append({
                'id': row['id'],
                'card_number': row['card_number'],
                'amount': int(row['amount']) if row['amount'] else None,
                'card_ttl': row['card_ttl'].strftime('%m.%Y') if row['card_ttl'] else None,
                'cvc': row['cvc'],
                'comment': row['comment'],
                'email': row['email'],
                'is_safe': row['is_safe'],
            })

        return result

    async def _get_requested_payments(self, request):
        sort_by, sort_order, filter_by, filter_value = self._get_filter_query(request)

        result = []
        requested_payments = await self.db.get_requested_payments(filter_by, filter_value, sort_by, sort_order)

        for row in await requested_payments:
            result.append({
                'id': row['id'],
                'tax': row['tax'],
                'bic': row['bic'],
                'phone': row['phone'],
                'account_number': row['account_number'],
                'comment': row['comment'],
                'email': row['email'],
                'amount': int(row['amount']) if row['amount'] else None,
            })

        return result

    def _get_filter_query(self, request):
        sort_by = None
        sort_order = None
        filter_by = None
        filter_value = None

        if request.query.get('sort'):
            sort_by = request.query['field']
            sort_order = request.query['sort']
        elif request.query.get('filter'):
            filter_by = request.query['field']
            filter_value = self._parse_filter_value(filter_by, request)

        return sort_by, sort_order, filter_by, filter_value

    @staticmethod
    def _parse_filter_value(filter_by, request):
        filter_value = request.query['filter']

        if filter_by == 'card_ttl':
            filter_value = datetime.datetime.strptime(filter_value, '%m.%Y').date()
        elif filter_by == 'id' or filter_by == 'amount':
            filter_value = int(filter_value)

        return filter_value

    def _error(self):
        return self._json_response({
            'error': 'Something wrong',
        })
