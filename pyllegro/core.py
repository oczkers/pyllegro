# -*- coding: utf-8 -*-

"""
gshop.allegro
~~~~~~~~~~~~~~~~~~~

A lightweight wrapper around the Allegro webapi.

"""

import sys
import time
import logging
from pysimplesoap.client import SoapClient, SoapFault
from socket import error as socketError
from decimal import Decimal
from base64 import b64encode
from hashlib import sha256

# logger - pysiemplesoap
logger_pysimplesoap = logging.getLogger('pysimplesoap')
logger_pysimplesoap.setLevel(logging.ERROR)

# logger - allegro
logger_allegro = logging.getLogger('pyllegro')


def magicDecode(var):
    """Decodes unicode to string."""
    if var:
        var = var.encode('utf8')
    return var


def chunked(l, n):
    """Chunks one big list into few small lists."""
    return [l[i:i+n] for i in range(0, len(l), n)]


class Allegro(object):
    def __init__(self, username, passwd, webapi_key, debug=False):
        self.debug = debug
        self.webapi_url = 'https://webapi.allegro.pl/service.php?wsdl'
        self.username = username
        self.passwd_hash = b64encode(sha256(passwd).digest())  # hash password
        self.webapi_key = webapi_key
        # init soap client & login
        self.client = SoapClient(wsdl=self.webapi_url)
        self.token = self.login(self.username, self.passwd_hash, self.webapi_key)

    def __relogin__(self):
        """Forced logging. Returns token."""
        while True:
            try:
                return self.login(self.username, self.passwd_hash, self.webapi_key)
            except socketError as e:
                logger_allegro.warning(e)
                time.sleep(5)
            except SoapFault as e:
                logger_allegro.warning(e)
                time.sleep(5)
            except:
                logger_allegro.warning('Unknown login error')
                logger_allegro.exception(sys.exc_info())
                time.sleep(5)

    def __ask__(self, service, **kwargs):
        """Asks allegro (catch errors). Returns response."""
        if self.debug: print 'ALLEGRO:', service, kwargs  # DEBUG

        while True:
            if service not in ('doGetSiteJournalDeals', 'doGetSiteJournalDealsInfo'):
                kwargs['sessionHandle'] = self.token
            else:
                kwargs['sessionId'] = self.token
            # process only if token avaible
            try:
                return self.client.wsdl_call(service, **kwargs)
            except socketError as e:
                logger_allegro.warning(e)
                time.sleep(5)
            except SoapFault as e:
                if e[0] == 'ERR_SESSION_EXPIRED' or e[0] == 'ERR_NO_SESSION':
                    # logger_allegro.debug('Session expired - relogging.')
                    logger_allegro.debug(e)
                    self.token = self.__relogin__()
                elif e[0] == 'ERR_INTERNAL_SYSTEM_ERROR':
                    logger_allegro.debug(e)
                    time.sleep(5)
                # elif e[0] == 'ERR_AUCTION_KILLED': # deleted by allegro admin
                #    pass
                else:
                    logger_allegro.warning(e)
                    time.sleep(5)
                    self.token = self.__relogin__()
            except:
                logger_allegro.warning('Unknown soap error')
                logger_allegro.exception(sys.exc_info())
                time.sleep(5)
                self.token = self.__relogin__()

    def login(self, username, passwd_hash, webapi_key, country_code=1):
        """Logins (sets self.token). Returns token (session_handle)."""
        ver_key = self.client.doQuerySysStatus(1, 1, webapi_key)['verKey']
        return self.client.doLoginEnc(username, passwd_hash,
                                      country_code, webapi_key,
                                      ver_key)['sessionHandlePart']

    def getAuctionDetails(self, auction_id):
        """Returns basic auction details (doShowItemInfoExt)."""
        return self.__ask__('doShowItemInfoExt',
                            itemId=auction_id,
                            # getDesc=0,
                            # getImageUrl=0,
                            # getAttribs=0,
                            # getPostageOptions=0,
                            # getCompanyInfo=0
                            )  # ['itemListInfoExt']

    def getBids(self, auction_id):
        """Returns bids."""
        bids = {}
        rc = self.__ask__('doGetBidItem2', itemId=auction_id)['biditemList']
        for i in rc:
            i = i['item']['bidsArray']
            bids[long(i[1]['item'])] = {
                'price': Decimal(i[6]['item']),
                'quantity': int(i[5]['item']),
                'date_buy': i[7]['item']
            }
        return bids

    def getOrders(self, auction_ids):
        """Returns orders details."""
        orders = {}
        # chunk list (only 25 auction_ids per request)
        for chunk in chunked(auction_ids, 25):
            auctions = [{'item': auction_id} for auction_id in chunk]
            rc = self.__ask__('doGetPostBuyData', itemsArray=auctions)['itemsPostBuyData']  # [0]['item']
            for auction in rc:
                auction = auction['item']
                orders_auction = []
                bids = self.getBids(auction['itemId'])
                # get orders details
                for i in auction.get('usersPostBuyData', ()):
                    i = i['item']['userData']
                    if i['userId'] not in bids: continue  # temporary(?) webapi bug fix
                    orders_auction.append({
                        'allegro_aid': auction['itemId'],
                        'allegro_uid': i['userId'],
                        'allegro_login': magicDecode(i['userLogin']),
                        'name': magicDecode(i['userFirstName']),
                        'surname': magicDecode(i['userLastName']),
                        'company': magicDecode(i['userCompany']),
                        'postcode': magicDecode(i['userPostcode']),
                        'city': magicDecode(i['userCity']),
                        'address': magicDecode(i['userAddress']),
                        'email': magicDecode(i['userEmail']),
                        'phone': i['userPhone'],
                        'price': bids[i['userId']]['price'],
                        'quantity': bids[i['userId']]['quantity'],
                        'date_buy': bids[i['userId']]['date_buy']
                    })
                orders[auction['itemId']] = orders_auction
        return orders

    def getTotalPaid(self, auction_id, buyer_id):
        """Returns total paid from buyer on single auction."""
        # TODO: it has to be better way to check payments.
        date_end = long(time.time())
        date_start = date_end - 60 * 60 * 24 * 90
        rc = self.__ask__('doGetMyIncomingPayments',
                          buyerId=buyer_id,
                          itemId=auction_id,
                          transRecvDateFrom=date_start,
                          transRecvDateTo=date_end,
                          transPageLimit=25,  # notneeded | TODO: can be more than 25 payments
                          transOffset=0)['payTransIncome']
        paid = 0
        for t in rc:
            t = t['item']
            if t['payTransStatus'] == u'Zakończona' and t['payTransIncomplete'] == 0:
                if t['payTransItId'] == 0:  # wplata laczna
                    for td in t['payTransDetails']:
                        td = td['item']
                        if td['payTransDetailsItId'] == auction_id:
                            paid += Decimal(str(td['payTransDetailsPrice']))
                else:  # wplata pojedyncza
                    paid += Decimal(str(t['payTransAmount']))
        return paid

    def getJournalDealsInfo(self, start=0):
        """Returns all deals ammount (from start)."""
        rc = self.__ask__('doGetSiteJournalDealsInfo',
                          journalStart=start)['siteJournalDealsInfo']
        return rc['dealEventsCount']

    def getJournalDeals(self, start=0):
        """Returns all journal deals from start."""
        deals = []
        while self.getJournalDealsInfo(start) > 0:
            rc = self.__ask__('doGetSiteJournalDeals',
                              journalStart=start)['siteJournalDeals']
            for i in rc:
                deals.append({
                    'deal_id': i['item']['dealId'],
                    'deal_status': i['item']['dealEventType'],
                    'transaction_id': i['item']['dealTransactionId'],
                    'time': i['item']['dealEventTime'],
                    'event_id': i['item']['dealEventId'],
                    'auction_id': i['item']['dealItemId'],
                    'buyer_id': i['item']['dealBuyerId'],
                    'quantity': i['item']['dealQuantity']
                })
            start = rc[-1]['item']['dealEventId']
        return deals

    # feedback
    def getWaitingFeedbacks(self):
        """Returns all waiting feedbacks from buyers."""
        # TODO: return sorted dictionary (negative/positive/neutral)
        feedbacks = []
        offset = 0
        amount = self.__ask__('doGetWaitingFeedbacksCount')['feedbackCount']
        while amount > 0:
            rc = self.__ask__('doGetWaitingFeedbacks',
                              offset=offset, packageSize=200)
            feedbacks.extend(rc['feWaitList'])
            amount -= 200
            offset += 1
        return feedbacks

    def doFeedback(self, item_id, use_comment_template, buyer_id, comment, comment_type, op):
        """http://allegro.pl/webapi/documentation.php/show/id,42"""
        return self.__ask__('doFeedback',
                            feItemId=item_id,
                            feUseCommentTemplate=use_comment_template,
                            feToUserId=buyer_id,
                            feComment=comment,
                            feCommentType=comment_type,
                            feOp=op)['feedbackId']

    # refund
    def doSendRefundForms(self, item_id, buyer_id, reason, quantity_sold):
        """http://allegro.pl/webapi/documentation.php/show/id,201"""
        # TODO: deprecated
        return self.__ask__('doSendRefundForms',
                            sendRefundFormsDataArr={
                                'item': {
                                    'itemId': item_id, 'buyerId': buyer_id,
                                    'refundReason': reason, 'itemQuantitySold': quantity_sold
                                }
                            })['sendRefundFormsResultsArr']
