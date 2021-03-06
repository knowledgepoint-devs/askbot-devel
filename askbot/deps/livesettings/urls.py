try:
    from django.conf.urls import *
except ImportError:
    from django.conf.urls.defaults import *

urlpatterns = patterns('askbot.deps.livesettings.views',
    url(r'^$', 'site_settings', {}, name='site_settings'),
    url(r'^export/$', 'export_as_yaml', {}, name='settings_export'),
    url(r'^(?P<group>[^/]+)/$', 'group_settings', name='group_settings'),
)
