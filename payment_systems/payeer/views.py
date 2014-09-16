from decimal import Decimal
import hashlib
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404
from . import NAME
from django.views.decorators.csrf import csrf_exempt
from default_set.models import *
from default_set.transactions import UserTransaction
from payment_systems.perfect_money.pm import PerfectMoney
from django.db import transaction
from django.utils.translation import ugettext_lazy as _
import base64
import requests
import json


def deposit_continue(request, tr, context):
    description = base64.b64encode((settings.PROJECT_TITLE + ' deposit').encode('utf-8')).decode('utf-8')
    arHash = (
        settings.PAYEER_SHOP_ID,
        str(tr.pk),
        str(tr.amount),
        'USD',
        description,
        settings.PAYEER_SHOP_KEY
    )
    context['sign'] = hashlib.sha256(':'.join(arHash).encode('utf-8')).hexdigest().upper()
    context['description'] = description
    return render(request, NAME + '/continue.html', context)


@csrf_exempt
def deposit_result(request):
    if not request.method == 'POST':
        return HttpResponse('no')

    retval = 'error'
    if request.POST.get('m_operation_id', None) and request.POST.get('m_sign', None):

        arHash = (
            request.POST['m_operation_id'],
            request.POST['m_operation_ps'],
            request.POST['m_operation_date'],
            request.POST['m_operation_pay_date'],
            request.POST['m_shop'],
            request.POST['m_orderid'],
            request.POST['m_amount'],
            request.POST['m_curr'],
            request.POST['m_desc'],
            request.POST['m_status'],
            settings.PAYEER_SHOP_KEY
        )
        order_id = request.POST.get('m_orderid')
        sign_hash = hashlib.sha256(':'.join(arHash).encode('utf-8')).hexdigest().upper()
        if request.POST.get('m_sign') == sign_hash and request.POST.get('m_status') == 'success':
            retval = order_id + '|success'
            tr = Transaction.objects.get(pk=int(request.POST.get('m_orderid')))
            if tr.amount == Decimal(request.POST.get('m_amount')) and tr.ps == NAME and not tr.is_ended:
                transaction = UserTransaction()
                transaction.do_transaction_deposit(tr.user, tr, request.POST.get('m_operation_id'))
            else:
                retval = order_id + "|error"
        else:
            retval = order_id + "|error"

    return HttpResponse(retval)


@transaction.atomic
@user_passes_test(lambda u: u.is_superuser)
def transaction_withdraw(tr):
    tr = Transaction.objects.select_for_update().get(pk=tr.pk)
    if tr.transaction_type == TRANSACTION_WITHDRAW and not tr.is_ended:
        transfer = requests.post('https://payeer.com/ajax/api/api.php',
                                 {'action': 'transfer', 'account': settings.PAYEER_ACCOUNT,
                                  'apiId': settings.PAYEER_API_ID, 'apiPass': settings.PAYEER_API_KEY,
                                  'curIn': 'USD', 'sum': tr.get_abs_amount(), 'curOut': 'USD',
                                  'to': tr.get_wallet(), 'comment': settings.PROJECT_TITLE + ' withdrawal'})
        tr.batch = json.loads(transfer.text)['historyId']
        tr.is_ended = True
        tr.save()
        tr.user.email_user(_('Вывод средств в проекте {}').format(settings.PROJECT_TITLE),
                           'mail/withdrawal_end.html', dict(transaction=tr))
