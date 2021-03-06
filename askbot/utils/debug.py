"""debugging utilities"""
import datetime
import sys
from django.conf import settings as django_settings

def debug(message):
    """print debugging message to stderr"""
    site_id = django_settings.SITE_ID
    message = "%s site_id=%s %s" % (
        datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
        site_id, 
        message)
    message = unicode(message).encode('utf-8')
    sys.stderr.write(message + '\n')
