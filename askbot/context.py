"""Askbot template context processor that makes some parameters
from the django settings, all parameters from the askbot livesettings
and the application available for the templates
"""
import sys
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils import simplejson
from django.middleware.csrf import get_token

import askbot
from askbot import api
from askbot import models
from askbot import const
from askbot.conf import settings as askbot_settings
from askbot.deps.django_authopenid.views import get_signin_view_context
from askbot.search.state_manager import DummySearchState
from askbot.search.state_manager import SearchState
from askbot.skins.loaders import get_skin
from askbot.utils import url_utils
from askbot.utils.slug import slugify
from askbot.utils.html import site_url
from askbot.utils.forms import get_feed
from askbot.utils.translation import get_language


def application_settings(request):
    """The context processor function"""
    if not request.path.startswith('/' + settings.ASKBOT_URL):
        #todo: this is a really ugly hack, will only work
        #when askbot is installed not at the home page.
        #this will not work for the
        #heavy modders of askbot, because their custom pages
        #will not receive the askbot settings in the context
        #to solve this properly we should probably explicitly
        #add settings to the context per page
        return {}
    my_settings = askbot_settings.as_dict()
    my_settings['LANGUAGE_CODE'] = getattr(request, 'LANGUAGE_CODE', settings.LANGUAGE_CODE)
    my_settings['MULTILINGUAL'] = askbot.is_multilingual()
    my_settings['MULTISITE'] = askbot.is_multisite()
    my_settings['LANGUAGES_DICT'] = dict(getattr(settings, 'LANGUAGES', []))
    my_settings['ALLOWED_UPLOAD_FILE_TYPES'] = \
            settings.ASKBOT_ALLOWED_UPLOAD_FILE_TYPES
    my_settings['ASKBOT_URL'] = settings.ASKBOT_URL
    my_settings['STATIC_URL'] = settings.STATIC_URL
    my_settings['IP_MODERATION_ENABLED'] = getattr(settings, 'ASKBOT_IP_MODERATION_ENABLED', False)
    my_settings['ASKBOT_CSS_DEVEL'] = getattr(
                                        settings,
                                        'ASKBOT_CSS_DEVEL',
                                        False
                                    )
    my_settings['USE_LOCAL_FONTS'] = getattr(
                                        settings,
                                        'ASKBOT_USE_LOCAL_FONTS',
                                        False
                                    )

    my_settings['CSRF_COOKIE_NAME'] = settings.CSRF_COOKIE_NAME
    my_settings['DEBUG'] = settings.DEBUG
    my_settings['USING_RUNSERVER'] = 'runserver' in sys.argv
    my_settings['ASKBOT_VERSION'] = askbot.get_version()
    my_settings['LOGIN_REDIRECT_URL'] = settings.LOGIN_REDIRECT_URL
    my_settings['LOGIN_URL'] = url_utils.get_login_url()
    my_settings['LOGOUT_URL'] = url_utils.get_logout_url()

    if my_settings['EDITOR_TYPE'] == 'tinymce':
        tinymce_plugins = settings.TINYMCE_DEFAULT_CONFIG.get('plugins', '').split(',')
        my_settings['TINYMCE_PLUGINS'] = map(lambda v: v.strip(), tinymce_plugins)
    else:
        my_settings['TINYMCE_PLUGINS'] = [];

    my_settings['LOGOUT_REDIRECT_URL'] = url_utils.get_logout_redirect_url()

    use_askbot_login_sys = ('askbot.deps.django_authopenid' in settings.INSTALLED_APPS)
    my_settings['USE_ASKBOT_LOGIN_SYSTEM'] = use_askbot_login_sys

    current_language = get_language()

    #for some languages we will start searching for shorter words
    if current_language == 'ja':
        #we need to open the search box and show info message about
        #the japanese lang search
        min_search_word_length = 1
    else:   
        min_search_word_length = my_settings['MIN_SEARCH_WORD_LENGTH']

    context = {
        'base_url': site_url(''),
        'min_search_word_length': min_search_word_length,
        'current_language_code': current_language,
        'settings': my_settings,
        'skin': get_skin(),
        'moderation_items': api.get_info_on_moderation_items(request.user),
        'noscript_url': const.DEPENDENCY_URLS['noscript'],
        'dummy_search_state': DummySearchState(),
    }

    #add context for the modal login menu
    if my_settings['USE_ASKBOT_LOGIN_SYSTEM'] and request.user.is_anonymous():
        context.update(get_signin_view_context(request))

    feed = get_feed(request)
    search_state = (feed and SearchState(feed=feed) or DummySearchState())
    context['dummy_search_state'] = search_state
    context['feed'] = feed 

    if askbot_settings.GROUPS_ENABLED:
        #calculate context needed to list all the groups
        def _get_group_url(group):
            """calculates url to the group based on its id and name"""
            group_slug = slugify(group['name'])
            return reverse(
                'users_by_group',
                kwargs={'group_id': group['id'], 'group_slug': group_slug}
            )

        #load id's and names of all groups
        global_group = models.Group.objects.get_global_group()
        groups = models.Group.objects.exclude_personal()
        groups = groups.exclude(id=global_group.id)
        groups_data = list(groups.values('id', 'name'))

        #sort groups_data alphanumerically, but case-insensitive
        groups_data = sorted(
                        groups_data,
                        lambda x, y: cmp(x['name'].lower(), y['name'].lower())
                    )

        #insert data for the global group at the first position
        groups_data.insert(0, {'id': global_group.id, 'name': global_group.name})

        #build group_list for the context
        group_list = list()
        for group in groups_data:
            link = _get_group_url(group)
            group_list.append({'name': group['name'], 'link': link})
        context['group_list'] = simplejson.dumps(group_list)

        #todo: get default ask group from the default ask group of the default
        #space of the current feed. Right now we take default group by id per-site,
        #but it should be per feed
        default_ask_group_id = getattr(settings, 'DEFAULT_ASK_GROUP_ID', 0)
        default_ask_group_name = ''
        if default_ask_group_id != 0:
            default_group = models.Group.objects.get(id=default_ask_group_id)
            default_ask_group_name = default_group.name

        context['default_ask_group_id'] = default_ask_group_id
        context['default_ask_group_name'] = default_ask_group_name

    return context
