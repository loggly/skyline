from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.MIMEImage import MIMEImage
from smtplib import SMTP
import urllib2
import simplejson
import alerters
import settings
import logging
import re
import datetime

logger = logging.getLogger("AnalyzerLog")

"""
Create any alerter you want here. The function will be invoked from trigger_alert.
Two arguments will be passed, both of them tuples: alert and metric.

alert: the tuple specified in your settings:
    alert[0]: The matched substring of the anomalous metric
    alert[1]: the name of the strategy being used to alert
    alert[2]: The timeout of the alert that was triggered
metric: information about the anomaly itself
    metric[0]: the anomalous value
    metric[1]: The full name of the anomalous metric
"""

def dot_to_json(a):
    """
    Takes in a dictionary whose keys are in dot notation
    and returns another dictionary with keys as a JSON tree
    For example, it takes in: {'json.message.status.time':50, 'json.message.code.response':80, 'json.time':100}
    and will return: {'message': {'code': {'response': 80}, 'status': {'time': 50}}, 'time': 100}

    Note that the labels of end points cannot also be inner nodes

    See: http://stackoverflow.com/questions/25389875/dot-notation-to-json-in-python

    This function in essence does the following in a compact way:
    output = {}
    value = output
    value = value.setdefault('message',{})
    value = value.setdefault('code',{})
    value['status'] = 10
    print output     # gives: {'message': {'code': {'status': 10}}}
    """
    output = {}
    for key, value in a.iteritems():
        path = key.split('.')
        if path[0] == 'json':
            path = path[1:]
        target = reduce(lambda d, k: d.setdefault(k, {}), path[:-1], output)
        target[path[-1]] = value
    return output

def parse_metric_name(metric_name):
    m = re.match('id.(\d*)\.(.*)', metric_name)
    id = m.group(1)
    name = m.group(2)
    return id, name

def alert_loggly(alert, metric):

    """ Logs a JSON object to Loggly """
    loggly_key = settings.LOGGLY_OPTS['auth_token']
    tag = settings.LOGGLY_OPTS['tag']
    id, name = parse_metric_name(metric[1])
    value = metric[0]

    msg = {
        "id": id,
        "matched_substring":alert[0],
        "strategy_used":alert[1],
        "next_alert_in_sec":alert[2],
    }
    msg.update(dot_to_json({name:value}))

    log_data = "PLAINTEXT=" + urllib2.quote(simplejson.dumps(msg))

    uri = "https://logs-01.loggly.com/inputs/%s/tag/%s/" % (loggly_key, tag)
    logger.info("Sending to Loggly with\nURI: %s \nand data:%s" % (uri,log_data))

    # Send log data to Loggly
    urllib2.urlopen(uri, log_data)


def alert_smtp(alert, metric):

    # For backwards compatibility
    if '@' in alert[1]:
        sender = settings.ALERT_SENDER
        recipient = alert[1]
    else:
        sender = settings.SMTP_OPTS['sender']
        recipients = settings.SMTP_OPTS['recipients'][alert[0]]

    # Backwards compatibility
    if type(recipients) is str:
        recipients = [recipients]

    for recipient in recipients:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '[skyline alert] ' + metric[1]
        msg['From'] = sender
        msg['To'] = recipient
        link = settings.GRAPH_URL % (metric[1])
        body = 'Anomalous value: %s <br> Next alert in: %s sec <a href="%s"><img src="%s"/></a>'\
               % (metric[0], alert[2], link, link)
        msg.attach(MIMEText(body, 'html'))
        s = SMTP('127.0.0.1')
        s.sendmail(sender, recipient, msg.as_string())
        s.quit()


def alert_pagerduty(alert, metric):
    import pygerduty
    pager = pygerduty.PagerDuty(settings.PAGERDUTY_OPTS['subdomain'],
                                settings.PAGERDUTY_OPTS['auth_token'])
    pager.trigger_incident(settings.PAGERDUTY_OPTS['key'],
                           "Anomalous metric: %s (value: %s)" % (metric[1], metric[0]))


def alert_hipchat(alert, metric):
    import hipchat
    hipster = hipchat.HipChat(token=settings.HIPCHAT_OPTS['auth_token'])
    rooms = settings.HIPCHAT_OPTS['rooms'][alert[0]]

    now = datetime.datetime.utcnow().replace(second=0, microsecond=0)
    date_from = now - datetime.timedelta(seconds=50*60)
    date_until = now + datetime.timedelta(seconds=10*60)

    id, name = parse_metric_name(metric[1])
    value = metric[0]
    name = "json.%s" % name

    url_params = settings.GRAPH_URL % (
                                        id, name,
                                        date_from.isoformat(),
                                        date_until.isoformat())

    url_params = url_params.replace(":","%3A").replace(" ","%20")

    link = 'http://' + settings.LOGGLY_HOST + url_params

    for room in rooms:
        hipster.method('rooms/message', method='POST',
                       parameters={'room_id': room, 'from': 'Skyline',
                                   'color': settings.HIPCHAT_OPTS['color'],
                                   'message': 'Anomaly: %s: %s <BR>'
                                              'Link: %s' % (
                                              name, value, link)})


def trigger_alert(alert, metric):
    logger.info("Sending alert with %s for %s" % (alert, metric))

    if '@' in alert[1]:
        strategy = 'alert_smtp'
    else:
        strategy = 'alert_' + alert[1]

    getattr(alerters, strategy)(alert, metric)
