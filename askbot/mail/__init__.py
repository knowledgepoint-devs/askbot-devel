"""functions that send email in askbot
these automatically catch email-related exceptions
"""
from django.conf import settings as django_settings
DEBUG_EMAIL = getattr(django_settings, 'ASKBOT_DEBUG_EMAIL', False)

import os
import re
import smtplib
import sys
from askbot import exceptions
from askbot import const
from askbot.conf import settings as askbot_settings
from askbot.mail import parsing
from askbot.utils import url_utils
from askbot.utils.debug import debug
from askbot.utils.file_utils import store_file
from askbot.utils.html import absolutize_urls
from askbot.utils.html import get_text_from_html
from django.contrib.sites.models import Site
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.forms import ValidationError
from django.template import Context
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.utils.translation import ugettext as _
from django.utils.translation import ugettext_lazy
from django.utils.translation import string_concat
from urlparse import urlparse

#todo: maybe send_mail functions belong to models
#or the future API
def prefix_the_subject_line(subject):
    """prefixes the subject line with the
    EMAIL_SUBJECT_LINE_PREFIX either from
    from live settings, which take default from django
    """
    prefix = askbot_settings.EMAIL_SUBJECT_PREFIX
    if prefix != '':
        subject = prefix.strip() + ' ' + subject.strip()
    return subject

def extract_first_email_address(text):
    """extract first matching email address
    from text string
    returns ``None`` if there are no matches
    """
    match = const.EMAIL_REGEX.search(text)
    if match:
        return match.group(0)
    else:
        return None

def thread_headers(post, orig_post, update):
    """modify headers for email messages, so
    that emails appear as threaded conversations in gmail"""
    suffix_id = django_settings.SERVER_EMAIL
    if update == const.TYPE_ACTIVITY_ASK_QUESTION:
        msg_id = "NQ-%s-%s" % (post.id, suffix_id)
        headers = {'Message-ID': msg_id}
    elif update == const.TYPE_ACTIVITY_ANSWER:
        msg_id = "NA-%s-%s" % (post.id, suffix_id)
        orig_id = "NQ-%s-%s" % (orig_post.id, suffix_id)
        headers = {'Message-ID': msg_id,
                  'In-Reply-To': orig_id}
    elif update == const.TYPE_ACTIVITY_UPDATE_QUESTION:
        msg_id = "UQ-%s-%s-%s" % (post.id, post.last_edited_at, suffix_id)
        orig_id = "NQ-%s-%s" % (orig_post.id, suffix_id)
        headers = {'Message-ID': msg_id,
                  'In-Reply-To': orig_id}
    elif update == const.TYPE_ACTIVITY_COMMENT_QUESTION:
        msg_id = "CQ-%s-%s" % (post.id, suffix_id)
        orig_id = "NQ-%s-%s" % (orig_post.id, suffix_id)
        headers = {'Message-ID': msg_id,
                  'In-Reply-To': orig_id}
    elif update == const.TYPE_ACTIVITY_UPDATE_ANSWER:
        msg_id = "UA-%s-%s-%s" % (post.id, post.last_edited_at, suffix_id)
        orig_id = "NQ-%s-%s" % (orig_post.id, suffix_id)
        headers = {'Message-ID': msg_id,
                  'In-Reply-To': orig_id}
    elif update == const.TYPE_ACTIVITY_COMMENT_ANSWER:
        msg_id = "CA-%s-%s" % (post.id, suffix_id)
        orig_id = "NQ-%s-%s" % (orig_post.id, suffix_id)
        headers = {'Message-ID': msg_id,
                  'In-Reply-To': orig_id}
    else:
        # Unknown type -> Can't set headers
        return {}

    return headers

def _send_mail(subject_line, body_text, sender_email, recipient_list, headers=None):
    """base send_mail function, which will attach email in html format
    if html email is enabled"""
    html_enabled = askbot_settings.HTML_EMAIL_ENABLED
    if html_enabled:
        message_class = mail.EmailMultiAlternatives
    else:
        message_class = mail.EmailMessage

    from askbot.models import User
    email_list = list()
    for recipient in recipient_list:
        if isinstance(recipient, User):
            email_list.append(recipient.email)
        else:
            email_list.append(recipient)

    exclude_set = set(getattr(django_settings, 'ASKBOT_NO_EMAIL_ADDRESSES', ()))
    email_set = set(email_list)
    email_set = email_set - exclude_set
    email_list = list(email_set)

    msg = message_class(
                subject_line,
                get_text_from_html(body_text),
                sender_email,
                email_list,
                headers = headers
            )
    if html_enabled:
        msg.attach_alternative(body_text, "text/html")
    msg.send()

def send_mail(
            subject_line=None,
            body_text=None,
            from_email=None,
            recipient=None,
            headers=None,
            raise_on_failure=False,
        ):
    """
    todo: remove parameters not relevant to the function
    sends email message
    logs email sending activity
    and any errors are reported as critical
    in the main log file

    if raise_on_failure is True, exceptions.EmailNotSent is raised
    """
    from askbot import models
    if isinstance(recipient, basestring):
        base_url = askbot_settings.APP_URL
        recipient_address = recipient
    elif isinstance(recipient, models.User):
        base_url = recipient.get_default_site_base_url()
        recipient_address = recipient.email
    else:
        raise TypeError('unexpected type for "recipient" %s' % type(recipient))

    body_text = absolutize_urls(body_text, base_url)
    from_email = from_email or askbot_settings.ADMIN_EMAIL or \
                                    django_settings.DEFAULT_FROM_EMAIL
    try:
        assert(subject_line is not None)
        subject_line = prefix_the_subject_line(subject_line)
        _send_mail(
            subject_line,
            body_text,
            from_email,
            [recipient_address,],
            headers=headers
        )
        if DEBUG_EMAIL:
            debug('sent update to %s' % recipient_address)
    except Exception, error:
        if DEBUG_EMAIL:
            debug(error)
        if raise_on_failure == True:
            raise exceptions.EmailNotSent(unicode(error))

def mail_moderators(
            subject_line = '',
            body_text = '',
            raise_on_failure = False,
            headers = None
        ):
    """sends email to forum moderators and admins
    """
    base_url = askbot_settings.APP_URL
    body_text = absolutize_urls(body_text, base_url)
    from django.db.models import Q
    from askbot.models import User
    recipient_list = User.objects.filter(
                    Q(status='m') | Q(is_superuser=True)
                ).filter(
                    is_active = True
                ).values_list('email', flat=True)
    recipient_list = set(recipient_list)

    _send_mail(
        subject_line,
        body_text,
        getattr(django_settings, 'DEFAULT_FROM_EMAIL', ''),
        recipient_list,
        headers=headers
    )


INSTRUCTIONS_PREAMBLE = ugettext_lazy('<p>To post by email, please:</p>')
QUESTION_TITLE_INSTRUCTION = ugettext_lazy(
    '<li>Type title in the subject line</li>'
)
QUESTION_DETAILS_INSTRUCTION = ugettext_lazy(
    '<li>Type details into the email body</li>'
)
OPTIONAL_TAGS_INSTRUCTION = ugettext_lazy(
"""<li>The beginning of the subject line can contain tags,
<em>enclosed in the square brackets</em> like so: [Tag1; Tag2]</li>"""
)
REQUIRED_TAGS_INSTRUCTION = ugettext_lazy(
"""<li>In the beginning of the subject add at least one tag
<em>enclosed in the brackets</em> like so: [Tag1; Tag2].</li>"""
)
TAGS_INSTRUCTION_FOOTNOTE = ugettext_lazy(
"""<p>Note that a tag may consist of more than one word, to separate
the tags, use a semicolon or a comma, for example, [One tag; Other tag]</p>"""
)

def bounce_email(
    email, subject, reason = None, body_text = None, reply_to = None
):
    """sends a bounce email at address ``email``, with the subject
    line ``subject``, accepts several reasons for the bounce:
    * ``'problem_posting'``, ``unknown_user`` and ``permission_denied``
    * ``body_text`` in an optional parameter that allows to append
      extra text to the message
    """
    if reason == 'problem_posting':
        error_message = _(
            '<p>Sorry, there was an error while processing your message '
            'please contact the %(site)s administrator</p>'
        ) % {'site': askbot_settings.APP_SHORT_NAME}

        if askbot_settings.TAGS_ARE_REQUIRED:
            error_message = string_concat(
                                    INSTRUCTIONS_PREAMBLE,
                                    '<ul>',
                                    QUESTION_TITLE_INSTRUCTION,
                                    REQUIRED_TAGS_INSTRUCTION,
                                    QUESTION_DETAILS_INSTRUCTION,
                                    '</ul>',
                                    TAGS_INSTRUCTION_FOOTNOTE
                                )
        else:
            error_message = string_concat(
                                    INSTRUCTIONS_PREAMBLE,
                                    '<ul>',
                                        QUESTION_TITLE_INSTRUCTION,
                                        QUESTION_DETAILS_INSTRUCTION,
                                        OPTIONAL_TAGS_INSTRUCTION,
                                    '</ul>',
                                    TAGS_INSTRUCTION_FOOTNOTE
                                )

    elif reason == 'unknown_user':
        error_message = _(
            '<p>Sorry, in order to make posts to %(site)s '
            'by email, please <a href="%(url)s">register first</a></p>'
        ) % {
            'site': askbot_settings.APP_SHORT_NAME,
            'url': url_utils.get_login_url()
        }
    elif reason == 'permission_denied' and body_text is None:
        error_message = _(
            '<p>Sorry, your post could not be made by email '
            'due to insufficient privileges of your user account</p>'
        )
    elif body_text:
        error_message = body_text
    else:
        raise ValueError('unknown reason to bounce an email: "%s"' % reason)


    #print 'sending email'
    #print email
    #print subject
    #print error_message
    headers = {}
    if reply_to:
        headers['Reply-To'] = reply_to

    send_mail(
        recipient=email,
        subject_line='Re: ' + subject,
        body_text=error_message,
        headers=headers
    )

def extract_reply(text):
    """take the part above the separator
    and discard the last line above the separator
    ``text`` is the input text
    """
    return parsing.extract_reply_contents(
                                text,
                                const.REPLY_SEPARATOR_REGEX
                            )

def process_attachment(attachment):
    """will save a single
    attachment and return
    link to file in the markdown format and the
    file storage object
    """
    file_storage, file_name, file_url = store_file(attachment)
    markdown_link = '[%s](%s) ' % (attachment.name, file_url)
    file_extension = os.path.splitext(attachment.name)[1]
    #todo: this is a hack - use content type
    if file_extension.lower() in ('.png', '.jpg', '.jpeg', '.gif'):
        markdown_link = '!' + markdown_link
    return markdown_link, file_storage

def extract_user_signature(text, reply_code):
    """extracts email signature as text trailing
    the reply code"""
    stripped_text = strip_tags(text)

    signature = ''
    if reply_code in stripped_text:
        #extract the signature
        tail = list()
        for line in reversed(stripped_text.splitlines()):
            #scan backwards from the end until the magic line
            if reply_code in line:
                break
            tail.insert(0, line)

        #strip off the leading quoted lines, there could be one or two
        #also strip empty lines
        while tail and (tail[0].startswith('>') or tail[0].strip() == ''):
            tail.pop(0)

        signature = '\n'.join(tail)

    #another hack: remove the inline images from the attachment
    #a better solution is to refactor the code to allow inclusion
    #of images in the signatures. The issue is images are "re-uploaded"
    #every time effectively changing the signature.
    img_re = re.compile(r'\[[^]]+\]\(/upfiles/[^)]+\)')
    signature = img_re.sub('', signature)

    #patch signature to a sentinel value if it is truly empty, because we
    #cannot allow empty signature field, which indicates no
    #signature at all and in that case we ask user to create one
    return signature or 'empty signature'


def process_parts(parts, reply_code=None, from_address=None):
    """Uploads the attachments and parses out the
    body, if body is multipart.
    Links to attachments will be added to the body of the question.
    Returns ready to post body of the message and the list
    of uploaded files.
    """
    body_text = ''
    stored_files = list()
    attachments_markdown = ''

    if DEBUG_EMAIL:
        debug('--- MESSAGE PARTS:\n\n')

    for (part_type, content) in parts:
        if part_type == 'attachment':
            if DEBUG_EMAIL:
                debug('REGULAR ATTACHMENT:\n')
            markdown, stored_file = process_attachment(content)
            stored_files.append(stored_file)
            attachments_markdown += '\n\n' + markdown
        elif part_type == 'body':
            if DEBUG_EMAIL:
                debug('BODY:\n' + content)
            body_text += '\n\n' + content.strip('\n\t ')
        elif part_type == 'inline':
            if DEBUG_EMAIL:
                debug('INLINE ATTACHMENT:\n')
            markdown, stored_file = process_attachment(content)
            stored_files.append(stored_file)
            attachments_markdown += '\n\n' + markdown

    if DEBUG_EMAIL:
        debug('--- THE END\n')

    #if the response separator is present -
    #split the body with it, and discard the "so and so wrote:" part
    if reply_code and reply_code in body_text:
        #todo: maybe move this part out
        signature = extract_user_signature(body_text, reply_code)
        body_text = extract_reply(body_text)
    else:
        #1) signature cannot be detected, b/c there is no reply code
        #   to designate what text comes after as a signature
        #2) we will not attempt to extract reply that comes 
        #   before the reply separator line (above the quote)
        #   presense of this will usually coincide with the presence
        #   of the reply_code in the email body
        signature = None

    if from_address:
        body_text = parsing.strip_trailing_sender_references(
                                                        body_text,
                                                        from_address
                                                    )

    return body_text.strip(), stored_files, attachments_markdown, signature


def process_emailed_question(
    from_address, subject, body_text, stored_files, stored_files_body_text,
    tags=None, space_name=None, email_host=None
):
    """posts question received by email or bounces the message"""
    #a bunch of imports here, to avoid potential circular import issues
    from askbot.forms import AskByEmailForm
    from askbot.models import ReplyAddress, User
    from askbot.mail import messages

    reply_to = None
    try:
        #todo: delete uploaded files when posting by email fails!!!
        data = {
            'sender': from_address,
            'subject': subject,
            'body_text': body_text,
            'email_host': email_host,
            'space': space_name
        }
        user = User.objects.get(email__iexact=from_address)
        form = AskByEmailForm(data, user=user)
        if form.is_valid():
            email_address = form.cleaned_data['email']

            if user.can_post_by_email() is False:
                raise PermissionDenied(messages.insufficient_reputation(user))

            body_text = form.cleaned_data['body_text']

            stripped_body_text = user.strip_email_signature(body_text)

            #note that signature '' means it is unset and 'empty signature' is a sentinel
            #because there is no other way to indicate unset signature without adding
            #another field to the user model
            signature_changed = (
                stripped_body_text == body_text and
                user.email_signature != 'empty signature'
            )

            need_new_signature = (
                user.email_isvalid is False or
                user.email_signature == '' or
                signature_changed
            )
            
            #ask for signature response if user's email has not been
            #validated yet or if email signature could not be found
            if need_new_signature:

                if DEBUG_EMAIL:
                    debug('FAILED SIGNATURE IN:\n%s\n' % body_text)
                    debug('CURRENT SIGNATURE:\n%s\n' % user.email_signature)
                    debug('USER ID %d\n' % user.id)

                reply_to = ReplyAddress.objects.create_new(
                    user = user,
                    reply_action = 'validate_email'
                ).as_email_address(prefix='welcome-')
                message = messages.ask_for_signature(user, footer_code = reply_to)
                raise PermissionDenied(message)

            tagnames = form.cleaned_data['tagnames']

            #defect - here we might get "too many tags" issue
            if tags:
                tagnames += ' ' + ' '.join(tags)

            space = form.cleaned_data['space']

            question = user.post_question(
                title=form.cleaned_data['title'],
                tags=tagnames.strip(),
                space=space,
                body_text=stripped_body_text + stored_files_body_text,
                by_email=True,
                email_address=from_address,
                group_id=space.get_default_ask_group_id()
            )
            from askbot.models import signals
            signals.new_question_posted.send(None,
                question=question,
                user=user,
                form_data=form.cleaned_data
            )
        else:
            raise ValidationError()

    except User.DoesNotExist:
        bounce_email(email_address, subject, reason = 'unknown_user')
    except User.MultipleObjectsReturned:
        bounce_email(email_address, subject, reason = 'problem_posting')
    except PermissionDenied, error:
        bounce_email(
            email_address,
            subject,
            reason = 'permission_denied',
            body_text = unicode(error),
            reply_to = reply_to
        )
    except ValidationError:
        if from_address:
            bounce_email(
                from_address,
                subject,
                reason = 'problem_posting',
            )


def send_email_key(email, key, handler_url_name='user_account_recover'):
    """private function. sends email containing validation key
    to user's email address
    """
    subject = _("Recover your %(site)s account") % \
                {'site': askbot_settings.APP_SHORT_NAME}

    url = urlparse(askbot_settings.APP_URL)
    data = {
        'validation_link': url.scheme + '://' + url.netloc + \
                            reverse(handler_url_name) +\
                            '?validation_code=' + key,
        'site_name': askbot_settings.APP_SHORT_NAME
    }
    template = get_template('authopenid/email_validation.html')
    message = template.render(data)#todo: inject language preference
    send_mail(subject, message, django_settings.DEFAULT_FROM_EMAIL, email)
