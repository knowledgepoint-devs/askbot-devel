import datetime
import askbot
import traceback
from django.contrib.sites.models import Site
from django.core.management.base import NoArgsCommand
from django.core.urlresolvers import reverse
from django.db import connection
from django.db.models import Q, F
from askbot.models import User, Post, PostRevision, Thread
from askbot.models import Activity, EmailFeedSetting
from askbot.models import Space
from django.template.loader import get_template
from django.utils.translation import ugettext as _
from django.utils.translation import ungettext
from django.utils.translation import activate as activate_language
from django.conf import settings as django_settings
from askbot.conf import settings as askbot_settings
from django.utils.datastructures import SortedDict
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from askbot import const
from askbot import mail
from askbot.utils.slug import slugify

DEBUG_THIS_COMMAND = False
PRINT_DEBUG_MESSAGES = False

def get_all_origin_posts(mentions):
    origin_posts = set()
    for mention in mentions:
        post = mention.content_object
        origin_posts.add(post.get_origin_post())
    return list(origin_posts)

#todo: refactor this as class
def extend_question_list(
                    src, dst, cutoff_time = None, 
                    limit=False, add_mention=False,
                    add_comment = False,
                    languages=None
                ):
    """src is a query set with questions
    or None
    dst - is an ordered dictionary
    update reporting cutoff time for each question
    to the latest value to be more permissive about updates
    """
    if src is None:#is not QuerySet
        return #will not do anything if subscription of this type is not used
    if limit and len(dst.keys()) >= askbot_settings.MAX_ALERTS_PER_EMAIL:
        return
    if cutoff_time is None:
        if hasattr(src, 'cutoff_time'):
            cutoff_time = src.cutoff_time
        else:
            raise ValueError('cutoff_time is a mandatory parameter')

    for q in src:
        if languages and q.language_code not in languages:
            continue
        if q in dst:
            meta_data = dst[q]
        else:
            meta_data = {'cutoff_time': cutoff_time}
            dst[q] = meta_data

        if cutoff_time > meta_data['cutoff_time']:
            #the latest cutoff time wins for a given question
            #if the question falls into several subscription groups
            #this makes mailer more eager in sending email
            meta_data['cutoff_time'] = cutoff_time
        if add_mention:
            if 'mentions' in meta_data:
                meta_data['mentions'] += 1
            else:
                meta_data['mentions'] = 1
        if add_comment:
            if 'comments' in meta_data:
                meta_data['comments'] += 1
            else:
                meta_data['comments'] = 1

def format_action_count(string, number, output):
    if number > 0:
        output.append(_(string) % {'num':number})

class Command(NoArgsCommand):
    def handle_noargs(self, **options):
        self.site = Site.objects.get_current()

        if askbot_settings.ENABLE_EMAIL_ALERTS:
            activate_language(django_settings.LANGUAGE_CODE)
            template = get_template('email/delayed_email_alert.html')

            for user in User.objects.filter(askbot_profile__default_site=self.site):
                try:
                    self.send_email_alerts(user, template)
                except Exception:
                    self.report_exception(user)
        connection.close()

    def format_debug_msg(self, user, content):
        msg = u"%s site_id=%d user=%s: %s" % ( 
            datetime.datetime.now().strftime('%y-%m-%d %h:%m:%s'),
            self.site.id,
            repr(user.username),
            content
        )
        return msg.encode('utf-8')

    def print_debug_msg(self, user, msg=''):
        if PRINT_DEBUG_MESSAGES:
            print self.format_debug_msg(user, msg)

    def report_exception(self, user):
        """reports exception that happened during sending email alert to user"""
        message = self.format_debug_msg(user, traceback.format_exc())
        print message
        admin_email = askbot_settings.ADMIN_EMAIL 
        try:
            subject_line = u"Error processing daily/weekly notification for User '%s' for Site '%s'" % (user.username, self.site.id)
            mail.send_mail(
                subject_line=subject_line.encode('utf-8'),
                body_text=message,
                recipient_list=[admin_email,]
            )
        except:
            message = u"ERROR: was unable to report this exception to %s: %s" % (admin_email, traceback.format_exc())
            print self.format_debug_msg(user, message)
        else: 
            message = u"Sent email reporting this exception to %s" % admin_email
            print self.format_debug_msg(user, message)

    def get_updated_questions_for_user(self, user):
        """
        retreive relevant question updates for the user
        according to their subscriptions and recorded question
        views
        """

        user_feeds = EmailFeedSetting.objects.filter(
                                                subscriber=user
                                            ).exclude(
                                                frequency__in=('n', 'i')
                                            )

        should_proceed = False
        for feed in user_feeds:
            if feed.should_send_now() == True:
                should_proceed = True
                break

        #shortcircuit - if there is no ripe feed to work on for this user
        if should_proceed == False:
            self.print_debug_msg(user, "no reports due")
            return {}
        self.print_debug_msg(user, "one or more reports is due")

        #these are placeholders for separate query sets per question group
        #there are four groups - one for each EmailFeedSetting.feed_type
        #and each group has subtypes A and B
        #that's because of the strange thing commented below
        #see note on Q and F objects marked with todo tag
        q_sel_A = None
        q_sel_B = None

        q_ask_A = None
        q_ask_B = None

        q_ans_A = None
        q_ans_B = None

        q_all_A = None
        q_all_B = None

        #base question query set for this user
        #basic things - not deleted, not closed, not too old
        #not last edited by the same user

        base_qs = Post.objects.get_questions(user=user)
        self.print_debug_msg(user, "%d question posts in this user's groups (%s)" % (base_qs.count(), user.groups.all().values_list('name', flat=True)))

        if askbot_settings.SPACES_ENABLED and askbot.is_multisite():
            site = Site.objects.get_current()
            spaces = list(Space.objects.get_for_site(site=site))
            base_qs = base_qs.filter(thread__spaces__in=spaces)
        self.print_debug_msg(user, "%d question posts left after filtering by spaces" % base_qs.count())

        base_qs = base_qs.exclude(thread__last_activity_by=user)
        self.print_debug_msg(user, "%d question posts left after excluding those last updated by this user" % base_qs.count())
        base_qs = base_qs.exclude(thread__last_activity_at__lt=user.date_joined)#exclude old stuff
        self.print_debug_msg(user, "%d question posts left after excluding those last updated before this user joined" % base_qs.count())
        base_qs = base_qs.exclude(deleted=True)
        self.print_debug_msg(user, "%d question posts left after excluding those that are deleted" % base_qs.count())
        base_qs = base_qs.exclude(thread__closed=True).order_by('-thread__last_activity_at')
        self.print_debug_msg(user, "%d question posts left after excluding those whose Thread is closed" % base_qs.count())

        if askbot_settings.CONTENT_MODERATION_MODE == 'premoderation':
            base_qs = base_qs.filter(approved = True)
            self.print_debug_msg(user, "%d question posts left after limiting to approved posts only" % base_qs.count())
        #todo: for some reason filter on did not work as expected ~Q(viewed__who=user) | 
        #      Q(viewed__who=user,viewed__when__lt=F('thread__last_activity_at'))
        #returns way more questions than you might think it should
        #so because of that I've created separate query sets Q_set2 and Q_set3
        #plus two separate queries run faster!

        #build two two queries based

        #questions that are not seen by the user at all
        not_seen_qs = base_qs.filter(~Q(viewed__who=user))
        self.print_debug_msg(user, "%d of these question posts have not been viewed by this user" % not_seen_qs.count())
        #questions that were seen, but before last modification
        seen_before_last_mod_qs = base_qs.filter(
                                    Q(
                                        viewed__who=user,
                                        viewed__when__lt=F('thread__last_activity_at')
                                    )
                                )
        self.print_debug_msg(user, "%d of these question posts were last viewed by this user before last update" % seen_before_last_mod_qs.count())

        #shorten variables for convenience
        Q_set_A = not_seen_qs
        Q_set_B = seen_before_last_mod_qs

        if askbot.is_multilingual():
            languages = user.get_languages()
        else:
            languages = None

        for feed in user_feeds:
            self.print_debug_msg(user, "examine feed %s" % repr(feed))
            if feed.feed_type == 'm_and_c':
                #alerts on mentions and comments are processed separately
                #because comments to questions do not trigger change of last_updated
                #this may be changed in the future though, see
                #http://askbot.org/en/question/96/
                continue

            #each group of updates represented by the corresponding
            #query set has it's own cutoff time
            #that cutoff time is computed for each user individually
            #and stored as a parameter "cutoff_time"

            #we won't send email for a given question if an email has been
            #sent after that cutoff_time
            if feed.should_send_now():
                self.print_debug_msg(user, "should_send_now=True")
                if DEBUG_THIS_COMMAND == False:
                    self.print_debug_msg(user, "mark_reported_now")
                    feed.mark_reported_now()
                else:
                    self.print_debug_msg(user, "don't mark_reported_now")
                cutoff_time = feed.get_previous_report_cutoff_time() 

                if feed.feed_type == 'q_sel':
                    q_sel_A = Q_set_A.filter(thread__followed_by=user)
                    q_sel_A.cutoff_time = cutoff_time #store cutoff time per query set
                    q_sel_B = Q_set_B.filter(thread__followed_by=user)
                    q_sel_B.cutoff_time = cutoff_time #store cutoff time per query set

                elif feed.feed_type == 'q_ask':
                    q_ask_A = Q_set_A.filter(author=user)
                    q_ask_A.cutoff_time = cutoff_time
                    q_ask_B = Q_set_B.filter(author=user)
                    q_ask_B.cutoff_time = cutoff_time

                elif feed.feed_type == 'q_ans':
                    q_ans_A = Q_set_A.filter(thread__posts__author=user, thread__posts__post_type='answer')
                    q_ans_A = q_ans_A[:askbot_settings.MAX_ALERTS_PER_EMAIL]
                    q_ans_A.cutoff_time = cutoff_time

                    q_ans_B = Q_set_B.filter(thread__posts__author=user, thread__posts__post_type='answer')
                    q_ans_B = q_ans_B[:askbot_settings.MAX_ALERTS_PER_EMAIL]
                    q_ans_B.cutoff_time = cutoff_time

                elif feed.feed_type == 'q_all':
                    q_all_A = user.get_tag_filtered_questions(Q_set_A)
                    q_all_B = user.get_tag_filtered_questions(Q_set_B)

                    q_all_A = q_all_A[:askbot_settings.MAX_ALERTS_PER_EMAIL]
                    q_all_B = q_all_B[:askbot_settings.MAX_ALERTS_PER_EMAIL]
                    q_all_A.cutoff_time = cutoff_time
                    q_all_B.cutoff_time = cutoff_time
            else:
                self.print_debug_msg(user, "should_send_now=False")

        #build ordered list questions for the email report
        q_list = SortedDict()

        #todo: refactor q_list into a separate class?
        extend_question_list(q_sel_A, q_list, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with q_sel_A" % len(q_list.keys()))
        extend_question_list(q_sel_B, q_list, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with q_sel_B" % len(q_list.keys()))

        #build list of comment and mention responses here
        #it is separate because posts are not marked as changed
        #when people add comments
        #mention responses could be collected in the loop above, but
        #it is inconvenient, because feed_type m_and_c bundles the two
        #also we collect metadata for these here
        try:
            feed = user_feeds.get(feed_type='m_and_c')
            if feed.should_send_now():
                cutoff_time = feed.get_previous_report_cutoff_time()
                comments = Post.objects.get_comments().filter(
                                            added_at__lt = cutoff_time,
                                        ).exclude(
                                            author = user
                                        )
                q_commented = list() 
                for c in comments:
                    post = c.parent
                    if post.author != user:
                        continue

                    #skip is post was seen by the user after
                    #the comment posting time
                    q_commented.append(post.get_origin_post())

                extend_question_list(
                                q_commented,
                                q_list,
                                cutoff_time=cutoff_time,
                                add_comment=True,
                                languages=languages
                            )
                self.print_debug_msg(user, "%d items in q_list after extending with q_commented" % len(q_list.keys()))

                mentions = Activity.objects.get_mentions(
                                                    mentioned_at__lt = cutoff_time,
                                                    mentioned_whom = user
                                                )

                #print 'have %d mentions' % len(mentions)
                #MM = Activity.objects.filter(activity_type = const.TYPE_ACTIVITY_MENTION)
                #print 'have %d total mentions' % len(MM)
                #for m in MM:
                #    print m

                #filter out mentions to admin comments, if recipient user
                #is not an admin or moderator
                if askbot_settings.ADMIN_COMMENTS_ENABLED:
                    filtered_mentions = list()
                    not_an_admin = (user.can_access_admin_comments() == False)
                    for mention in mentions:
                        mention_post = mention.content_object
                        if not_an_admin and mention_post.post_type.startswith('admin_'):
                            continue
                        filtered_mentions.append(mention)
                    mentions = filtered_mentions

                mention_posts = get_all_origin_posts(mentions)
                q_mentions_id = [q.id for q in mention_posts]

                q_mentions_A = Q_set_A.filter(id__in = q_mentions_id)
                q_mentions_A.cutoff_time = cutoff_time
                extend_question_list(
                    q_mentions_A,
                    q_list,
                    add_mention=True,
                    languages=languages
                )
                self.print_debug_msg(user, "%d items in q_list after extending with q_mentions_A" % len(q_list.keys()))

                q_mentions_B = Q_set_B.filter(id__in = q_mentions_id)
                q_mentions_B.cutoff_time = cutoff_time
                extend_question_list(
                    q_mentions_B,
                    q_list,
                    add_mention=True,
                    languages=languages
                )
                self.print_debug_msg(user, "%d items in q_list after extending with q_mentions_B" % len(q_list.keys()))
        except EmailFeedSetting.DoesNotExist:
            pass

        if user.email_tag_filter_strategy == const.INCLUDE_INTERESTING:
            extend_question_list(q_all_A, q_list, languages=languages)
            self.print_debug_msg(user, "%d items in q_list after extending with q_all_A" % len(q_list.keys()))
            extend_question_list(q_all_B, q_list, languages=languages)
            self.print_debug_msg(user, "%d items in q_list after extending with q_all_B" % len(q_list.keys()))

        extend_question_list(q_ask_A, q_list, limit=True, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with limited q_ask_A" % len(q_list.keys()))
        extend_question_list(q_ask_B, q_list, limit=True, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with limited q_ask_B" % len(q_list.keys()))

        extend_question_list(q_ans_A, q_list, limit=True, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with limited q_ans_A" % len(q_list.keys()))
        extend_question_list(q_ans_B, q_list, limit=True, languages=languages)
        self.print_debug_msg(user, "%d items in q_list after extending with limited q_ans_B" % len(q_list.keys()))

        if user.email_tag_filter_strategy == const.EXCLUDE_IGNORED:
            extend_question_list(q_all_A, q_list, limit=True, languages=languages)
            self.print_debug_msg(user, "%d items in q_list after extending with limited q_all_A" % len(q_list.keys()))
            extend_question_list(q_all_B, q_list, limit=True, languages=languages)
            self.print_debug_msg(user, "%d items in q_list after extending with limited q_all_B" % len(q_list.keys()))

        ctype = ContentType.objects.get_for_model(Post)
        EMAIL_UPDATE_ACTIVITY = const.TYPE_ACTIVITY_EMAIL_UPDATE_SENT

        #up to this point we still don't know if emails about
        #collected questions were sent recently
        #the next loop examines activity record and decides
        #for each question, whether it needs to be included or not
        #into the report

        for q, meta_data in q_list.items():
            #this loop edits meta_data for each question
            #so that user will receive counts on new edits new answers, etc
            #and marks questions that need to be skipped
            #because an email about them was sent recently enough

            #also it keeps a record of latest email activity per question per user
            try:
                #todo: is it possible to use content_object here, instead of
                #content type and object_id pair?
                update_info = Activity.objects.get(
                                            user=user,
                                            content_type=ctype,
                                            object_id=q.id,
                                            activity_type=EMAIL_UPDATE_ACTIVITY
                                        )
                emailed_at = update_info.active_at
            except Activity.DoesNotExist:
                update_info = Activity(
                                        user=user, 
                                        content_object=q, 
                                        activity_type=EMAIL_UPDATE_ACTIVITY
                                    )
                emailed_at = datetime.datetime(1970, 1, 1)#long time ago
            except Activity.MultipleObjectsReturned:
                raise Exception(
                                'server error - multiple question email activities '
                                'found per user-question pair'
                                )

            cutoff_time = meta_data['cutoff_time']#cutoff time for the question

            #skip question if we need to wait longer because
            #the delay before the next email has not yet elapsed
            #or if last email was sent after the most recent modification
            if emailed_at > cutoff_time or emailed_at > q.thread.last_activity_at:
                meta_data['skip'] = True
                continue

            #collect info on all sorts of news that happened after
            #the most recent emailing to the user about this question
            q_rev = q.revisions.filter(revised_at__gt=emailed_at)
            q_rev = q_rev.exclude(author=user)

            #now update all sorts of metadata per question
            meta_data['q_rev'] = len(q_rev)
            if len(q_rev) > 0 and q.added_at == q_rev[0].revised_at:
                meta_data['q_rev'] = 0
                meta_data['new_q'] = True
            else:
                meta_data['new_q'] = False
                
            new_ans = Post.objects.get_answers(user).filter(
                                            thread=q.thread,
                                            added_at__gt=emailed_at,
                                            deleted=False,
                                        )
            new_ans = new_ans.exclude(author=user)
            meta_data['new_ans'] = len(new_ans)

            ans_ids = Post.objects.get_answers(user).filter(
                                            thread=q.thread,
                                            added_at__gt=emailed_at,
                                            deleted=False,
                                        ).values_list(
                                            'id', flat = True
                                        )
            ans_rev = PostRevision.objects.filter(post__id__in = ans_ids)
            ans_rev = ans_rev.exclude(author=user).distinct()
            meta_data['ans_rev'] = len(ans_rev)

            comments = meta_data.get('comments', 0)
            mentions = meta_data.get('mentions', 0)

            #print meta_data
            #finally skip question if there are no news indeed
            if len(q_rev) + len(new_ans) + len(ans_rev) + comments + mentions == 0:
                meta_data['skip'] = True
                #print 'skipping'
            else:
                meta_data['skip'] = False
                #print 'not skipping'
                update_info.active_at = datetime.datetime.now() 
                if DEBUG_THIS_COMMAND == False:
                    update_info.save() #save question email update activity 
        #q_list is actually an ordered dictionary
        #print 'user %s gets %d' % (user.username, len(q_list.keys()))
        #todo: sort question list by update time
        return q_list 

    def send_email_alerts(self, user, template):
        """sends delayed email alerts for user"""
        user.add_missing_askbot_subscriptions()
        activate_language(user.get_primary_language())

        #todo: q_list is a dictionary, not a list
        q_list = self.get_updated_questions_for_user(user)
        if len(q_list.keys()) == 0:
            self.print_debug_msg(user, "STOP (no updated questions to report on)")
            return

        self.print_debug_msg(user, "%d updated questions to report on" % len(q_list.keys()))
        num_q = 0
        for question, meta_data in q_list.items():
            if meta_data['skip']:
                del q_list[question]
                self.print_debug_msg(user, "removing this from set: question=%s, meta_data=%s" % (repr(question), repr(meta_data)))
            else:
                num_q += 1
        self.print_debug_msg(user, "%d updated questions left to report on after removing the 'skip's" % num_q)
        if num_q > 0:
            threads = Thread.objects.filter(id__in=[qq.thread_id for qq in q_list.keys()])
            tag_summary = Thread.objects.get_tag_summary_from_threads(threads)

            question_count = len(q_list.keys())
            
            if tag_summary:
                subject_line = ungettext(
                    '%(question_count)d updated question about %(topics)s',
                    '%(question_count)d updated questions about %(topics)s',
                    question_count
                ) % {
                    'question_count': question_count,
                    'topics': tag_summary
                }
            else:
                subject_line = ungettext(
                    '%(question_count)d updated question',
                    '%(question_count)d updated questions',
                    question_count
                ) % {'question_count': question_count}

            items_added = 0
            items_unreported = 0
            questions_data = list()
            for q, meta_data in q_list.items():
                act_list = []
                if meta_data['skip']:
                    continue
                if items_added >= askbot_settings.MAX_ALERTS_PER_EMAIL:
                    items_unreported = num_q - items_added #may be inaccurate actually, but it's ok
                    break
                else:
                    items_added += 1
                    if meta_data['new_q']:
                        act_list.append(_('new question'))
                    format_action_count('%(num)d rev', meta_data['q_rev'], act_list)
                    format_action_count('%(num)d ans', meta_data['new_ans'], act_list)
                    format_action_count('%(num)d ans rev', meta_data['ans_rev'], act_list)
                    questions_data.append({
                        'url': q.get_absolute_url(as_full_url=True),
                        'info': ', '.join(act_list),
                        'title': q.thread.title
                    })
                    self.print_debug_msg(user, "including: q=%s, meta_data=%s" % (repr(q), repr(meta_data)))

            text = template.render({
                'recipient_user': user,
                'questions': questions_data,
                'name': user.username,
                'admin_email': askbot_settings.ADMIN_EMAIL,
                'site_name': askbot_settings.APP_SHORT_NAME,
                'is_multilingual': askbot.is_multilingual(),
            })

            if DEBUG_THIS_COMMAND == True:
                recipient = askbot_settings.ADMIN_EMAIL
            else:
                recipient = user

            mail.send_mail(
                subject_line=subject_line,
                body_text=text,
                recipient=recipient
            )
