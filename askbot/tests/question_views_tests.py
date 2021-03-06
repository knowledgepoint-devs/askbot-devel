from askbot.conf import settings as askbot_settings
from askbot import const
from askbot import is_multilingual as askbot_is_multilingual
from askbot.tests.utils import AskbotTestCase
from askbot import models
from askbot.utils.html import get_soup
from askbot.models import get_feed_url, Feed
from django.conf import settings as django_settings
from django.core.urlresolvers import reverse
from django.utils import simplejson


class PrivateQuestionViewsTests(AskbotTestCase):

    def setUp(self):
        self._backup = askbot_settings.GROUPS_ENABLED
        askbot_settings.update('GROUPS_ENABLED', True)
        self.user = self.create_user('user')
        self.group = models.Group.objects.create(
                        name='user\'s group', openness=models.Group.OPEN
                    )
        self.user.join_group(self.group)
        # Since May 2013, user must have a primary group in order to post privately
        self.user.askbot_profile.primary_group = self.group
        self.user.askbot_profile.save()
        self.q_target_group = models.Group.objects.create(
                        name='question target group', openness=models.Group.OPEN
                    )
        self.qdata = {
            'title': 'test question title',
            'text': 'test question text',
        }
        if askbot_is_multilingual():
            self.qdata['language'] = django_settings.LANGUAGE_CODE 
        self.client.login(user_id=self.user.id, method='force')

    def tearDown(self):
        askbot_settings.update('GROUPS_ENABLED', self._backup)

    def test_post_private_question(self):
        data = self.qdata
        data['post_privately'] = 'checked'
        response = self.client.post(get_feed_url('ask'), data=data, follow=True)

        question = models.Thread.objects.get()
        self.assertTrue(question.is_private())
        dom = get_soup(response.content)
        title = dom.find('h1').text
        self.assertTrue(unicode(const.POST_STATUS['private']) in title)
        self.assertEqual(question.title, self.qdata['title'])

        #private question is not accessible to unauthorized users
        self.client.logout()
        response = self.client.get(question._question_post().get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.content, '')
        #private question link is not shown on the main page
        #to unauthorized users
        response = self.client.get(reverse('questions', kwargs={'feed': Feed.objects.get_default().name}))
        self.assertFalse(self.qdata['title'] in response.content)
        #private question link is not shown on the poster profile
        #to the unauthorized users
        response = self.client.get(self.user.get_profile_url())
        self.assertFalse(self.qdata['title'] in response.content)


    def test_publicise_private_question(self):
        question = self.post_question(user=self.user, is_private=True)
        self.assertTrue(question.is_private())
        title = question.thread.get_title()
        self.assertTrue(unicode(const.POST_STATUS['private']) in title)

        data = self.qdata
        data['select_revision'] = 'false'
        data['text'] = 'edited question text'
        response1 = self.client.post(
            reverse('edit_question', kwargs={'id':question.id}),
            data=data
        )

        self.assertFalse(question.is_private())
        response2 = self.client.get(question.get_absolute_url())
        dom = get_soup(response2.content)
        title = dom.find('h1').text
        self.assertEqual(title, self.qdata['title'])

        self.client.logout()
        response = self.client.get(question.get_absolute_url())
        self.assertTrue('edited question text' in response.content)

    def test_privatize_public_question(self):
        question = self.post_question(user=self.user)
        self.assertFalse(question.is_private())
        title = question.thread.get_title()
        self.assertFalse(unicode(const.POST_STATUS['private']) in title)

        data = self.qdata
        data['post_privately'] = 'checked'
        data['select_revision'] = 'false'
        response1 = self.client.post(
            reverse('edit_question', kwargs={'id':question.id}),
            data=data
        )

        self.assertTrue(question.is_private())
        response2 = self.client.get(question.get_absolute_url())
        dom = get_soup(response2.content)
        title = dom.find('h1').text
        self.assertTrue(unicode(const.POST_STATUS['private']) in title)

    def test_private_checkbox_is_on_when_editing_private_question(self):
        question = self.post_question(user=self.user, is_private=True)
        self.assertTrue(question.is_private())
        
        response = self.client.get(
            reverse('edit_question', kwargs={'id':question.id})
        )
        dom = get_soup(response.content)
        checkbox = dom.find(
            'input', attrs={'type': 'checkbox', 'name': 'post_privately'}
        )
        self.assertNotEqual(checkbox, None, "post_privately checkbox not found in page")
        self.assertEqual(checkbox['checked'], 'checked')

    def test_private_checkbox_is_off_when_editing_public_question(self):
        question = self.post_question(user=self.user)
        self.assertFalse(question.is_private())

        response = self.client.get(
            reverse('edit_question', kwargs={'id':question.id})
        )
        dom = get_soup(response.content)
        checkbox = dom.find(
            'input', attrs={'type': 'checkbox', 'name': 'post_privately'}
        )
        self.assertNotEqual(checkbox, None, "post_privately checkbox not found in page")
        self.assertEqual(checkbox.get('checked', False), False)


class PrivateAnswerViewsTests(AskbotTestCase):

    def setUp(self):
        self._backup = askbot_settings.GROUPS_ENABLED
        askbot_settings.update('GROUPS_ENABLED', True)
        self.user = self.create_user('user')
        group = models.Group.objects.create(
            name='the group', openness=models.Group.OPEN
        )
        self.user.join_group(group)
        # Since May 2013, user must have a primary group in order to post privately
        self.user.askbot_profile.primary_group = group
        self.user.askbot_profile.save()
        self.question = self.post_question(user=self.user)
        self.client.login(user_id=self.user.id, method='force')

    def tearDown(self):
        askbot_settings.update('GROUPS_ENABLED', self._backup)

    def test_post_private_answer(self):
        response1 = self.client.post(
            reverse('answer', kwargs={'id': self.question.id}),
            data={'text': 'some answer text', 'post_privately': 'checked'}
        )
        answer = self.question.thread.get_answers(user=self.user)[0]
        self.assertFalse(models.Group.objects.get_global_group() in set(answer.groups.all()))
        self.client.logout()

        user2 = self.create_user('user2')
        self.client.login(user_id=user2.id, method='force')
        response = self.client.get(self.question.get_absolute_url())
        self.assertFalse('some answer text' in response.content)

        self.client.logout()
        response = self.client.get(self.question.get_absolute_url())
        self.assertFalse('some answer text' in response.content)

    def test_private_checkbox_is_on_when_editing_private_answer(self):
        answer = self.post_answer(
            question=self.question, user=self.user, is_private=True
        )
        response = self.client.get(
            reverse('edit_answer', kwargs={'id': answer.id})
        )
        dom = get_soup(response.content)
        checkbox = dom.find(
            'input', attrs={'type': 'checkbox', 'name': 'post_privately'}
        )
        self.assertEqual(checkbox['checked'], 'checked')

    def test_private_checkbox_is_off_when_editing_public_answer(self):
        answer = self.post_answer(question=self.question, user=self.user)
        self.assertTrue(models.Group.objects.get_global_group() in set(answer.groups.all()))
        response = self.client.get(
            reverse('edit_answer', kwargs={'id': answer.id})
        )
        dom = get_soup(response.content)
        checkbox = dom.find(
            'input', attrs={'type': 'checkbox', 'name': 'post_privately'}
        )
        self.assertEqual(checkbox.get('checked', False), False)

    def test_publicise_private_answer(self):
        answer = self.post_answer(
            question=self.question, user=self.user, is_private=True
        )
        self.assertFalse(models.Group.objects.get_global_group() in answer.groups.all())
        self.client.post(
            reverse('edit_answer', kwargs={'id': answer.id}),
            data={'text': 'edited answer text', 'select_revision': 'false'}
        )
        answer = self.reload_object(answer)
        self.assertTrue(models.Group.objects.get_global_group() in answer.groups.all())
        self.client.logout()
        response = self.client.get(self.question.get_absolute_url())
        self.assertTrue('edited answer text' in response.content)


    def test_privatize_public_answer(self):
        answer = self.post_answer(question=self.question, user=self.user)
        self.assertTrue(models.Group.objects.get_global_group() in answer.groups.all())
        self.client.post(
            reverse('edit_answer', kwargs={'id': answer.id}),
            data={
                'text': 'edited answer text',
                'post_privately': 'checked',
                'select_revision': 'false'
            }
        )
        #check that answer is not visible to the "everyone" group
        answer = self.reload_object(answer)
        self.assertFalse(models.Group.objects.get_global_group() in answer.groups.all())
        #check that countent is not seen by an anonymous user
        self.client.logout()
        response = self.client.get(self.question.get_absolute_url())
        self.assertFalse('edited answer text' in response.content)
