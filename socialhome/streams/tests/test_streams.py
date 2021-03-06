import random
from unittest import skip
from unittest.mock import patch, Mock, call

from django.contrib.auth.models import AnonymousUser
from django.db.models import Max

from socialhome.content.enums import ContentType
from socialhome.content.models import Content
from socialhome.content.tests.factories import (
    ContentFactory, PublicContentFactory, SiteContentFactory, SelfContentFactory, LimitedContentFactory)
from socialhome.streams.enums import StreamType
from socialhome.streams.streams import (
    BaseStream, FollowedStream, PublicStream, TagStream, add_to_redis, add_to_stream_for_users,
    update_streams_with_content, check_and_add_to_keys)
from socialhome.tests.utils import SocialhomeTestCase
from socialhome.users.tests.factories import UserFactory


@patch("socialhome.streams.streams.get_redis_connection")
@patch("socialhome.streams.streams.time.time", return_value=123.123)
class TestAddToRedis(SocialhomeTestCase):
    def test_adds_each_key(self, mock_time, mock_get):
        mock_hset = Mock()
        mock_zadd = Mock()
        mock_get.return_value = Mock(hset=mock_hset, zadd=mock_zadd, zrank=Mock(return_value=None))
        add_to_redis(Mock(id=2), Mock(id=1), ["spam", "eggs"])
        calls = [
            call("spam", 123, 2),
            call("eggs", 123, 2),
        ]
        self.assertEqual(mock_zadd.call_args_list, calls)
        calls = [
            call("spam:throughs", 2, 1),
            call("eggs:throughs", 2, 1),
        ]
        self.assertEqual(mock_hset.call_args_list, calls)

    def test_returns_on_no_keys(self, mock_time, mock_get):
        mock_zadd = Mock()
        mock_get.return_value = Mock(zadd=mock_zadd, zrank=Mock(return_value=None))
        add_to_redis(Mock(), Mock(), [])


class TestAddToStreamForUsers(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_local_and_remote_user()
        cls.content = PublicContentFactory()
        cls.profile.following.add(cls.content.author)
        cls.limited_content = LimitedContentFactory()
        cls.reply = PublicContentFactory(parent=cls.content)

    @patch("socialhome.streams.streams.add_to_redis")
    def test_calls_add_to_redis(self, mock_add):
        add_to_stream_for_users(self.content.id, self.content.id, "FollowedStream")
        stream = FollowedStream(user=self.user)
        mock_add.assert_called_once_with(self.content, self.content, [stream.key])

    @patch("socialhome.streams.streams.check_and_add_to_keys")
    @patch("socialhome.streams.streams.CACHED_ANONYMOUS_STREAM_CLASSES", new=tuple())
    def test_calls_check_and_add_to_keys_for_each_user(self, mock_check):
        add_to_stream_for_users(self.content.id, self.content.id, "FollowedStream")
        mock_check.assert_called_once_with(FollowedStream, self.user, self.content, [])

    @skip("Add when anonymous user cached streams exist")
    @patch("socialhome.streams.streams.check_and_add_to_keys")
    def test_includes_anonymous_user_for_anonymous_user_streams(self, mock_check):
        add_to_stream_for_users(self.content.id, self.content.id, "ProfileAllStream")
        anon_call = mock_check.call_args_list[1]
        self.assertTrue(isinstance(anon_call[1], AnonymousUser))

    @patch("socialhome.streams.streams.Content.objects.filter")
    def test_returns_on_no_content_or_reply(self, mock_filter):
        add_to_stream_for_users(Content.objects.aggregate(max_id=Max("id")).get("max_id") + 1, Mock(), PublicStream)
        self.assertFalse(mock_filter.called)
        add_to_stream_for_users(self.reply.id, self.reply.id, PublicStream)
        self.assertFalse(mock_filter.called)

    @patch("socialhome.streams.streams.check_and_add_to_keys", return_value=True)
    def test_skips_if_not_cached_stream(self, mock_get):
        add_to_stream_for_users(self.content.id, self.content.id, "SpamStream")
        self.assertFalse(mock_get.called)
        add_to_stream_for_users(self.content.id, self.content.id, "PublicStream")
        self.assertFalse(mock_get.called)


class TestCheckAndAddToKeys(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_local_and_remote_user()
        cls.profile.following.add(cls.remote_profile)
        cls.content = PublicContentFactory()
        cls.remote_content = PublicContentFactory(author=cls.remote_profile)

    def test_adds_if_should_cache(self):
        self.assertEqual(
            check_and_add_to_keys(FollowedStream, self.user, self.remote_content, []),
            ["sh:streams:followed:%s" % self.user.id],
        )

    def test_does_not_add_if_shouldnt_cache(self):
        self.assertEqual(
            check_and_add_to_keys(FollowedStream, self.user, self.content, []),
            [],
        )


class TestUpdateStreamsWithContent(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_local_and_remote_user()
        cls.content = PublicContentFactory(author=cls.profile)
        cls.remote_content = PublicContentFactory()
        cls.share = PublicContentFactory(share_of=cls.content)

    @patch("socialhome.streams.streams.django_rq.enqueue")
    @patch("socialhome.streams.streams.add_to_redis")
    @patch("socialhome.streams.streams.CACHED_STREAM_CLASSES", new=(FollowedStream, PublicStream))
    def test_adds_with_local_user(self, mock_add, mock_enqueue):
        update_streams_with_content(self.remote_content)
        self.assertFalse(mock_add.called)
        update_streams_with_content(self.content)
        mock_add.assert_called_once_with(self.content, self.content, ["sh:streams:public:%s" % self.user.id])

    @patch("socialhome.streams.streams.django_rq.enqueue")
    def test_enqueues_each_stream_to_rq(self, mock_enqueue):
        update_streams_with_content(self.content)
        mock_enqueue.assert_called_once_with(
            add_to_stream_for_users, self.content.id, self.content.id, "FollowedStream",
        )

    @patch("socialhome.streams.streams.django_rq.enqueue")
    def test_enqueues_each_stream_to_rq__share(self, mock_enqueue):
        update_streams_with_content(self.share)
        mock_enqueue.assert_called_once_with(
            add_to_stream_for_users, self.content.id, self.share.id, "FollowedStream",
        )

    def test_returns_if_reply(self):
        self.assertIsNone(update_streams_with_content(Mock(content_type=ContentType.REPLY)))


@patch("socialhome.streams.streams.BaseStream.get_queryset", return_value=Content.objects.all())
class TestBaseStream(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory()
        cls.content1 = ContentFactory()
        cls.content2 = ContentFactory()

    def setUp(self):
        super().setUp()
        self.stream = BaseStream(user=self.user)

    def test___str__(self, mock_queryset):
        self.assertEqual(str(self.stream), "BaseStream (%s)" % str(self.user))

    @patch("socialhome.streams.streams.get_redis_connection")
    def test_get_cached_content_ids__calls(self, mock_get, mock_queryset):
        mock_redis = Mock(zrevrange=Mock(return_value=[]))
        mock_get.return_value = mock_redis
        self.stream.stream_type = StreamType.PUBLIC
        self.stream.get_cached_content_ids()
        # Skips zrevrank if not last_id
        self.assertFalse(mock_redis.zrevrank.called)
        # Calls zrevrange with correct parameters
        mock_redis.zrevrange.assert_called_once_with(self.stream.key, 0, self.stream.paginate_by)
        mock_redis.reset_mock()
        # Calls zrevrank with last_id
        self.stream.last_id = self.content2.id
        mock_redis.zrevrank.return_value = 3
        self.stream.get_cached_content_ids()
        mock_redis.zrevrank.assert_called_once_with(self.stream.key, self.content2.id)
        mock_redis.zrevrange.assert_called_once_with(self.stream.key, 4, 4 + self.stream.paginate_by)

    @patch("socialhome.streams.streams.get_redis_connection")
    def test_get_cached_content_ids__returns_empty_list_if_outside_cached_ids(self, mock_get, mock_queryset):
        mock_redis = Mock(zrevrank=Mock(return_value=None))
        mock_get.return_value = mock_redis
        self.stream.stream_type = StreamType.PUBLIC
        self.stream.last_id = 123
        self.assertEqual(self.stream.get_cached_content_ids(), ([], {}))
        self.assertFalse(mock_redis.zrevrange.called)

    @patch("socialhome.streams.streams.get_redis_connection")
    def test_get_cached_range(self, mock_get, mock_queryset):
        self.stream.stream_type = StreamType.PUBLIC
        mock_zrevrange = Mock(return_value=[str(self.content2.id), str(self.content1.id)])
        mock_hmget = Mock(return_value=[str(self.content2.id), str(self.content1.id)])
        mock_redis = Mock(zrevrange=mock_zrevrange, hmget=mock_hmget)
        mock_get.return_value = mock_redis
        ids, throughs = self.stream.get_cached_range(0)
        self.assertEqual(ids, [self.content2.id, self.content1.id])
        self.assertEqual(throughs, {self.content2.id: self.content2.id, self.content1.id: self.content1.id})
        mock_zrevrange.assert_called_once_with(self.stream.key, 0, 0 + self.stream.paginate_by)
        mock_hmget.assert_called_once_with(BaseStream.get_throughs_key(self.stream.key), keys=[
            self.content2.id, self.content1.id,
        ])

        # Non-zero index
        mock_zrevrange.reset_mock()
        self.stream.get_cached_range(5)
        mock_zrevrange.assert_called_once_with(self.stream.key, 5, 5 + self.stream.paginate_by)

    def test_get_content(self, mock_queryset):
        qs, throughs = self.stream.get_content()
        self.assertEqual(set(qs), {self.content2, self.content1})
        self.assertEqual(throughs, {self.content2.id: self.content2.id, self.content1.id: self.content1.id})

        self.stream.last_id = self.content2.id
        qs, throughs = self.stream.get_content()
        self.assertEqual(set(qs), {self.content1})
        self.assertEqual(throughs, {self.content1.id: self.content1.id})

        self.stream.last_id = self.content1.id
        qs, throughs = self.stream.get_content()
        self.assertFalse(qs)
        self.assertFalse(throughs)

    def test_get_content_ids__returns_right_ids_according_to_last_id_and_ordering(self, mock_queryset):
        ids, throughs = self.stream.get_content_ids()
        self.assertEqual(ids, [self.content2.id, self.content1.id])
        self.assertEqual(throughs, {self.content2.id: self.content2.id, self.content1.id: self.content1.id})

        self.stream.last_id = self.content2.id
        ids, throughs = self.stream.get_content_ids()
        self.assertEqual(ids, [self.content1.id])
        self.assertEqual(throughs, {self.content1.id: self.content1.id})

        # Reverse
        self.stream.ordering = "created"
        self.stream.last_id = None

        ids, throughs = self.stream.get_content_ids()
        self.assertEqual(ids, [self.content1.id, self.content2.id])
        self.assertEqual(throughs, {self.content1.id: self.content1.id, self.content2.id: self.content2.id})

        self.stream.last_id = self.content1.id
        ids, throughs = self.stream.get_content_ids()
        self.assertEqual(ids, [self.content2.id])
        self.assertEqual(throughs, {self.content2.id: self.content2.id})

        self.stream.last_id = self.content2.id
        ids, throughs = self.stream.get_content_ids()
        self.assertFalse(ids)
        self.assertFalse(throughs)

    def test_get_content_ids__limits_by_paginate_by(self, mock_queryset):
        self.stream.paginate_by = 1
        ids, throughs = self.stream.get_content_ids()
        self.assertEqual(ids, [self.content2.id])
        self.assertEqual(throughs, {self.content2.id: self.content2.id})

    def test_get_content_ids__returns_cached_ids_if_enough_in_cache(self, mock_queryset):
        stream = FollowedStream(user=self.user)
        stream.paginate_by = 1
        with patch.object(stream, "get_queryset") as mock_queryset, \
                patch.object(stream, "get_cached_content_ids") as mock_cached:
            mock_cached.return_value = [self.content1.id], {self.content1.id: self.content1.id}
            stream.get_content_ids()
            self.assertEqual(mock_queryset.call_count, 0)

    def test_init(self, mock_queryset):
        stream = BaseStream(last_id=333, user="user")
        self.assertEqual(stream.last_id, 333)
        self.assertEqual(stream.user, "user")

    @patch("socialhome.streams.streams.get_redis_connection", return_value="redis")
    def test_init_redis_connection(self, mock_redis, mock_queryset):
        stream = BaseStream()
        self.assertIsNone(stream.redis)
        stream.init_redis_connection()
        mock_redis.assert_called_once_with()
        self.assertEqual(stream.redis, "redis")
        mock_redis.reset_mock()
        stream.init_redis_connection()
        self.assertFalse(mock_redis.called)

    def test_should_cache_content(self, mock_queryset):
        self.assertTrue(self.stream.should_cache_content(self.content1))
        self.assertTrue(self.stream.should_cache_content(self.content2))
        mock_queryset.return_value = Content.objects.none()
        self.assertFalse(self.stream.should_cache_content(self.content1))
        self.assertFalse(self.stream.should_cache_content(self.content2))


class TestFollowedStream(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_local_and_remote_user()
        cls.remote_profile.followers.add(cls.profile)
        cls.create_content_set(author=cls.remote_profile)
        cls.other_public_content = PublicContentFactory()
        SiteContentFactory()
        SelfContentFactory()
        LimitedContentFactory()

    def setUp(self):
        super().setUp()
        self.stream = FollowedStream(user=self.user)

    def test_get_content_ids__uses_cached_ids(self):
        with patch.object(self.stream, "get_cached_content_ids", return_value=([], {})) as mock_cached:
            self.stream.get_content_ids()
            mock_cached.assert_called_once_with()

    def test_get_content_ids__fills_in_non_cached_content_up_to_pagination_amount(self):
        with patch.object(self.stream, "get_cached_content_ids") as mock_cached:
            cached_ids = random.sample(range(10000, 100000), self.stream.paginate_by - 1)
            throughs = dict(zip(cached_ids, cached_ids))
            mock_cached.return_value = cached_ids, throughs
            # Fills up with one of the two that are available
            all_ids = set(cached_ids + [self.site_content.id])
            self.assertEqual(set(self.stream.get_content_ids()[0]), all_ids)

    def test_get_throughs_key(self):
        self.assertEqual(
            self.stream.get_throughs_key(self.stream.key), "sh:streams:followed:%s:throughs" % self.user.id,
        )

    def test_key(self):
        self.assertEqual(self.stream.key, "sh:streams:followed:%s" % self.user.id)

    def test_only_followed_profile_content_returned(self):
        qs, _throughs = self.stream.get_content()
        self.assertEqual(
            set(qs),
            {self.public_content, self.site_content},
        )

    def test_raises_if_no_user(self):
        self.stream.user = None
        with self.assertRaises(AttributeError):
            self.stream.get_content()

    def test_should_cache_content(self):
        self.assertTrue(self.stream.should_cache_content(self.public_content))
        self.assertTrue(self.stream.should_cache_content(self.site_content))
        self.assertFalse(self.stream.should_cache_content(self.limited_content))
        self.assertFalse(self.stream.should_cache_content(self.self_content))
        self.assertFalse(self.stream.should_cache_content(self.other_public_content))


class TestPublicStream(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory()
        cls.create_content_set()

    def setUp(self):
        super().setUp()
        self.stream = PublicStream(user=self.user)

    def test_get_content_ids_does_not_use_cached_ids(self):
        with patch.object(self.stream, "get_cached_content_ids") as mock_cached:
            self.stream.get_content_ids()
            self.assertFalse(mock_cached.called)

    def test_key(self):
        self.assertEqual(self.stream.key, "sh:streams:public:%s" % self.user.id)
        stream = PublicStream(user=AnonymousUser())
        self.assertEqual(stream.key, "sh:streams:public:anonymous")

    def test_only_public_content_returned(self):
        qs, _throughs = self.stream.get_content()
        self.assertEqual(
            set(qs),
            {self.public_content},
        )

    def test_should_cache_content(self):
        self.assertTrue(self.stream.should_cache_content(self.public_content))
        self.assertFalse(self.stream.should_cache_content(self.site_content))
        self.assertFalse(self.stream.should_cache_content(self.limited_content))
        self.assertFalse(self.stream.should_cache_content(self.self_content))


class TestTagStream(SocialhomeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_local_and_remote_user()
        cls.local_user = UserFactory()
        cls.create_content_set()
        cls.public_tagged = PublicContentFactory(text="#foobar", author=cls.profile)
        cls.site_tagged = SiteContentFactory(text="#foobar", author=cls.profile)
        cls.self_tagged = SelfContentFactory(text="#foobar", author=cls.profile)
        cls.limited_tagged = LimitedContentFactory(text="#foobar", author=cls.profile)
        cls.tag = cls.public_tagged.tags.first()

    def setUp(self):
        super().setUp()
        self.stream = TagStream(tag=self.tag, user=self.user)
        self.anon_stream = TagStream(tag=self.tag, user=AnonymousUser())
        self.local_stream = TagStream(tag=self.tag, user=self.local_user)

    def test_get_content_ids_does_not_use_cached_ids(self):
        with patch.object(self.stream, "get_cached_content_ids") as mock_cached:
            self.stream.get_content_ids()
            self.assertFalse(mock_cached.called)

    def test_key(self):
        self.assertEqual(self.stream.key, "sh:streams:tag:%s" % self.user.id)
        stream = PublicStream(user=AnonymousUser())
        self.assertEqual(stream.key, "sh:streams:public:anonymous")

    def test_only_tagged_content_returned(self):
        qs, _throughs = self.anon_stream.get_content()
        self.assertEqual(
            set(qs),
            {self.public_tagged},
        )
        qs, _throughs = self.stream.get_content()
        self.assertEqual(
            set(qs),
            {self.public_tagged, self.site_tagged, self.self_tagged, self.limited_tagged},
        )
        qs, _throughs = self.local_stream.get_content()
        self.assertEqual(
            set(qs),
            {self.public_tagged, self.site_tagged},
        )

    def test_raises_if_no_user(self):
        self.stream.user = None
        with self.assertRaises(AttributeError):
            self.stream.get_content()

    def test_raises_if_no_tag(self):
        self.stream.tag = None
        with self.assertRaises(AttributeError):
            self.stream.get_content()

    def test_should_cache_content(self):
        # self.user stream
        self.assertTrue(self.stream.should_cache_content(self.public_tagged))
        self.assertTrue(self.stream.should_cache_content(self.site_tagged))
        self.assertTrue(self.stream.should_cache_content(self.limited_tagged))
        self.assertTrue(self.stream.should_cache_content(self.self_tagged))
        self.assertFalse(self.stream.should_cache_content(self.public_content))
        self.assertFalse(self.stream.should_cache_content(self.site_content))
        self.assertFalse(self.stream.should_cache_content(self.limited_content))
        self.assertFalse(self.stream.should_cache_content(self.self_content))
        # anon stream
        self.assertTrue(self.anon_stream.should_cache_content(self.public_tagged))
        self.assertFalse(self.anon_stream.should_cache_content(self.site_tagged))
        self.assertFalse(self.anon_stream.should_cache_content(self.limited_tagged))
        self.assertFalse(self.anon_stream.should_cache_content(self.self_tagged))
        self.assertFalse(self.anon_stream.should_cache_content(self.public_content))
        self.assertFalse(self.anon_stream.should_cache_content(self.site_content))
        self.assertFalse(self.anon_stream.should_cache_content(self.limited_content))
        self.assertFalse(self.anon_stream.should_cache_content(self.self_content))
        # self.local_user stream
        self.assertTrue(self.local_stream.should_cache_content(self.public_tagged))
        self.assertTrue(self.local_stream.should_cache_content(self.site_tagged))
        self.assertFalse(self.local_stream.should_cache_content(self.limited_tagged))
        self.assertFalse(self.local_stream.should_cache_content(self.self_tagged))
        self.assertFalse(self.local_stream.should_cache_content(self.public_content))
        self.assertFalse(self.local_stream.should_cache_content(self.site_content))
        self.assertFalse(self.local_stream.should_cache_content(self.limited_content))
        self.assertFalse(self.local_stream.should_cache_content(self.self_content))
