# -*- coding: utf-8 -*-

"""
gshop.allegro
~~~~~~~~~~~~~~~~~~~

A lightweight wrapper around the Allegro webapi.

"""

import sys
import time
import logging
from zeep import Client
from zeep.exceptions import Fault
from requests.exceptions import ConnectionError
# from socket import error as socketError
from decimal import Decimal
from base64 import b64encode
from hashlib import sha256

if sys.version_info[0] == 3:
    long = int

# logger - zeep
logger_zeep = logging.getLogger('zeep')
logger_zeep.setLevel(logging.ERROR)

# logger - allegro
logger_allegro = logging.getLogger(__name__)


def magicDecode(var):
    """Decode unicode to string."""
    if var:
        var = var.encode('utf8')
    return var


def chunked(l, n):
    """Chunk one big list into few small lists."""
    return [l[i:i + n] for i in range(0, len(l), n)]


class Allegro(object):
    def __init__(self, username, passwd, webapi_key, debug=False):
        self.debug = debug
        self.webapi_url = 'https://webapi.allegro.pl/service.php?wsdl'
        self.username = username
        self.passwd_hash = b64encode(sha256(passwd.encode('utf-8')).digest()).decode('utf-8')  # hash password
        self.webapi_key = webapi_key
        self.last_event_id = 0
        # init soap client & login
        # self.client = Client(self.webapi_url)
        # self.ArrayOfLong = self.client.get_type('ns0:ArrayOfLong')  # this should be done by zeep...
        self.token = self.login(self.username, self.passwd_hash, self.webapi_key)

    def __relogin__(self):
        """Forced logging. Returns token."""
        while True:
            try:
                return self.login(self.username, self.passwd_hash, self.webapi_key)
            # except socketError as e:
            #     logger_allegro.warning(e)
            #     time.sleep(5)
            except:
                print(sys.exc_info())
                print('Unknown login error')
                logger_allegro.warning('Unknown login error')
                logger_allegro.exception(sys.exc_info())
                time.sleep(5)

    def __ask__(self, service, **kwargs):
        """Ask allegro (catch errors). Returns response."""
        # TODO: send error code/message to mail
        if self.debug:
            print('ALLEGRO: %s %s' % (service, kwargs))  # DEBUG

        while True:
            if service not in ('doGetSiteJournalDeals', 'doGetSiteJournalDealsInfo'):
                kwargs['sessionHandle'] = self.token
            else:
                kwargs['sessionId'] = self.token
            # process only if token avaible
            try:
                return self.client.service[service](**kwargs)
            except Fault as e:
                if e.code in ('ERR_NO_SESSION', 'ERR_SESSION_EXPIRED'):
                    print('zly identyfikator, relogowanie')
                    time.sleep(5)
                    self.token = self.__relogin__()
                elif e.code == 'ERR_INTERNAL_SYSTEM_ERROR':
                    print('internal server error')
                    time.sleep(5)
                else:
                    print(sys.exc_info())
                    print(e)
                    print(e.code)
                    time.sleep(5)
                    self.token = self.__relogin__()
            except ConnectionError as e:
                print('connection error')
                print(e)
                time.sleep(5)
            # except socketError as e:
            #     logger_allegro.warning(e)
            #     time.sleep(5)
            # except SoapFault as e:
            #     if e[0] == 'ERR_SESSION_EXPIRED' or e[0] == 'ERR_NO_SESSION':
            #         # logger_allegro.debug('Session expired - relogging.')
            #         logger_allegro.debug(e)
            #         self.token = self.__relogin__()
            #     elif e[0] == 'ERR_INTERNAL_SYSTEM_ERROR':
            #         logger_allegro.debug(e)
            #         time.sleep(5)
            #     # elif e[0] == 'ERR_AUCTION_KILLED': # deleted by allegro admin
            #     #    pass
            #     else:
            #         logger_allegro.warning(e)
            #         time.sleep(5)
            #         self.token = self.__relogin__()
            except:
                print(sys.exc_info())
                print('Unknown soap error')
                logger_allegro.warning('Unknown soap error')
                logger_allegro.exception(sys.exc_info())
                time.sleep(5)
                self.token = self.__relogin__()

    def login(self, username, passwd_hash, webapi_key, country_code=1):
        """Log in (sets self.token). Returns token (session_handle)."""
        self.client = Client(self.webapi_url)
        self.ArrayOfLong = self.client.get_type('ns0:ArrayOfLong')  # this should be done by zeep...
        ver_key = self.client.service.doQuerySysStatus(1, 1, webapi_key)['verKey']
        return self.client.service.doLoginEnc(username, passwd_hash,
                                              country_code, webapi_key,
                                              ver_key)['sessionHandlePart']

    def getAuctionDetails(self, auction_id):
        """Return basic auction details (doShowItemInfoExt)."""
        return self.__ask__('doShowItemInfoExt',
                            itemId=auction_id,
                            # getDesc=0,
                            # getImageUrl=0,
                            # getAttribs=0,
                            # getPostageOptions=0,
                            # getCompanyInfo=0
                            )  # ['itemListInfoExt']

    def getBids(self, auction_id):
        """Retrieve all bids in given auction."""
        bids = {}
        rc = self.__ask__('doGetBidItem2', itemId=auction_id)
        if rc:
            for i in rc:
                i = i['bidsArray']
                bids[long(i['item'][1])] = {
                    'price': Decimal(i['item'][6]),
                    'quantity': int(i['item'][5]),
                    'date_buy': i['item'][7]
                }
        return bids

    def getBuyerInfo(self, auction_id, buyer_id):
        """Return buyer info."""
        # TODO: add price from getBids
        rc = self.__ask__('doGetPostBuyData', itemsArray=self.ArrayOfLong([auction_id]), buyerFilterArray=self.ArrayOfLong([buyer_id]))
        rc = rc[0]['usersPostBuyData']['item'][0]['userData']
        return {'allegro_aid': auction_id,
                'allegro_uid': rc['userId'],
                'allegro_login': magicDecode(rc['userLogin']),
                'name': magicDecode(rc['userFirstName']),
                'surname': magicDecode(rc['userLastName']),
                'company': magicDecode(rc['userCompany']),
                'postcode': magicDecode(rc['userPostcode']),
                'city': magicDecode(rc['userCity']),
                'address': magicDecode(rc['userAddress']),
                'email': magicDecode(rc['userEmail']),
                'phone': rc['userPhone']}

    def getOrders(self, auction_ids):
        """Return orders details."""
        orders = {}
        # chunk list (only 25 auction_ids per request)
        for chunk in chunked(auction_ids, 25):
            # auctions = [{'item': auction_id} for auction_id in chunk]  # TODO?: is it needed?
            auctions = self.ArrayOfLong(chunk)
            rc = self.__ask__('doGetPostBuyData', itemsArray=auctions)
            for auction in rc:
                orders_auction = []
                bids = self.getBids(auction['itemId'])
                # get orders details
                # for i in auction.get('usersPostBuyData', ()):
                if not auction['usersPostBuyData']:  # empty
                    continue
                for i in auction['usersPostBuyData']['item']:
                    i = i['userData']
                    if i['userId'] not in bids:  # temporary(?) webapi bug fix
                        continue
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
        """Return total paid from buyer on single auction."""
        # TODO: it has to be better way to check payments.
        date_end = long(time.time())
        date_start = date_end - 60 * 60 * 24 * 90
        rc = self.__ask__('doGetMyIncomingPayments',
                          buyerId=buyer_id,
                          itemId=auction_id,
                          transRecvDateFrom=date_start,
                          transRecvDateTo=date_end,
                          transPageLimit=25,  # notneeded | TODO: can be more than 25 payments
                          transOffset=0)
        paid = 0
        for t in (rc or []):
            # t = t['item']
            if t['payTransStatus'] == u'Zakończona' and t['payTransIncomplete'] == 0:
                if t['payTransItId'] == 0:  # wplata laczna
                    for td in t['payTransDetails']['item']:
                        if td['payTransDetailsItId'] == auction_id:
                            paid += Decimal(str(td['payTransDetailsPrice']))
                else:  # wplata pojedyncza
                    paid += Decimal(str(t['payTransAmount']))
        return paid

    def getJournal(self, start=0):
        """Get all journal events from start."""
        # TODO: while len(journaldeals) < 100
        pass

    def getJournalDealsInfo(self, start=0):
        """Return all events ammount (from start)."""
        rc = self.__ask__('doGetSiteJournalDealsInfo',
                          journalStart=start)
        return rc['dealEventsCount']

    def getJournalDeals(self, start=None):
        """Return all journal events from start."""
        # 1 - utworzenie aktu zakupowego (deala), 2 - utworzenie formularza pozakupowego (karta platnosci), 3 - anulowanie formularza pozakupowego (karta platnosci), 4 - zakończenie (opłacenie) transakcji przez PzA
        if start is not None:
            self.last_event_id = start
        events = []
        while self.getJournalDealsInfo(self.last_event_id) > 0:
            rc = self.__ask__('doGetSiteJournalDeals', journalStart=self.last_event_id)
            for i in rc:
                events.append({
                    'allegro_did': i['dealId'],
                    'deal_status': i['dealEventType'],
                    'transaction_id': i['dealTransactionId'],
                    'time': i['dealEventTime'],
                    'event_id': i['dealEventId'],
                    'allegro_aid': i['dealItemId'],
                    'allegro_uid': i['dealBuyerId'],
                    # 'seller_id': i['dealSellerId '],
                    'quantity': i['dealQuantity']
                })
            self.last_event_id = rc[-1]['dealEventId']
        return events

    # feedback
    def getWaitingFeedbacks(self):
        """Return all waiting feedbacks from buyers."""
        # TODO: return sorted dictionary (negative/positive/neutral)
        feedbacks = []
        offset = 0
        amount = self.__ask__('doGetWaitingFeedbacksCount')
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
