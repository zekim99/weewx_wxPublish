__author__ = 'Mike Zimmerman <mike@fishwheel.com>'
__license__ = 'Apache License, Version 2.0'
__copyright__ = 'Copyright 2015 Fishwheel'


import Queue
import socket
import sys
import syslog
import urllib
import urllib2
import datetime

import weewx
import weewx.restx
import weeutil.weeutil
from weeutil.weeutil import to_int, to_float, to_bool, timestamp_to_string, accumulateLeaves

class Fishwheel(weewx.restx.StdRESTful):

    def __init__(self, engine, config_dict):
        super(Fishwheel, self).__init__(engine, config_dict)

        # read configuration values to dictionary
        try:
            _fw_dict = weeutil.weeutil.accumulateLeaves(
                config_dict['StdRESTful']['Fishwheel'], max_level=1)
        except KeyError as exc:
            syslog.syslog(
                syslog.LOG_DEBUG, "restx: Fishwheel: "
                "Data will not be posted: Missing option %s" % exc
            )
            return

        _manager_dict = weewx.manager.get_manager_dict(
            config_dict['DataBindings'],
            config_dict['Databases'],
            'wx_binding'
        )

        self.loop_queue = Queue.Queue()
        self.loop_thread = FishwheelThread(
            self.loop_queue,
            _manager_dict,
            **_fw_dict
        )
        self.loop_thread.start()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)

    def new_loop_packet(self, event):
        self.loop_queue.put(event.packet)


class FishwheelThread(weewx.restx.RESTThread):

    DEFAULT_HOST = 'localhost'
    DEFAULT_PORT = '8080'
    DEFAULT_PREFIX = "wxupdate"

    def __init__(self, queue, manager_dict,
                 host=DEFAULT_HOST, port=DEFAULT_PORT, prefix=DEFAULT_PREFIX,
                 protocol_name="FW-Protocol",
                 post_interval=None, max_backlog=sys.maxint, stale=None,
                 log_success=True, log_failure=True,
                 timeout=10, max_tries=3, retry_wait=5):

        super(FishwheelThread, self).__init__(queue,
                                              protocol_name=protocol_name,
                                              manager_dict=manager_dict,
                                              post_interval=post_interval,
                                              max_backlog=max_backlog,
                                              stale=stale,
                                              log_success=log_success,
                                              log_failure=log_failure,
                                              timeout=timeout,
                                              max_tries=max_tries,
                                              retry_wait=retry_wait)

        self.host = host
        self.port = port
        self.prefix = prefix


    def convert_datetime(self, dt):
        if dt is not None:
            return str(datetime.datetime.utcfromtimestamp(dt))


    def process_record(self, record, dbmanager):
        _url = "http://" + self.host + ":" + str(self.port) + "/" + self.prefix
        syslog.syslog(syslog.LOG_DEBUG,
            "url is %s" % (_url)
        )

        # Get the full record by querying the database ...
        _full_record = self.get_record(record, dbmanager)
        # ... convert to US if necessary ...
        _us_record = weewx.units.to_US(_full_record)

        try:
            # fix datetimes
            _us_record['dateTime'] = self.convert_datetime(_us_record['dateTime'])
            _us_record['stormStart'] = self.convert_datetime(_us_record['stormStart'])
            _us_record['sunrise'] = self.convert_datetime(_us_record['sunrise'])
            _us_record['sunset'] = self.convert_datetime(_us_record['sunset'])

            # clean up JSON
            body = str(_us_record)
            body = str.replace(body, '\'', '\"')
            body = str.replace(body, 'None', 'null')
        except TypeError:
            syslog.syslog(syslog.LOG_INFO,
                'unexpected or missing value in payload.')
            return

        # headers
        headers = {
            'User-Agent': 'weewx/%s' % weewx.__version__,
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': 0,
            'Content-type': 'text/json'
        }

        try:
            req = urllib2.Request(_url, body, headers)
            res = urllib2.urlopen(req)
        except urllib2.HTTPError, e:
            syslog.syslog(syslog.LOG_INFO,
                'Unable to publish: [{0}], {1}'.format(e.code, e.reason))
            return
        except urllib2.URLError, e:
            syslog.syslog(syslog.LOG_INFO,
                'Unable to publish: {0}'.format(e.reason))
            return
