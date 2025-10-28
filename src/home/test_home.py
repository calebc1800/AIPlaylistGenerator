from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch
from home.models import Playlist, Song
from home.views import SpotifyAPIHelper


class PlaylistModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='testpass')

    def test_create_playlist(self):
        playlist = Playlist.objects.create(
            name='Test Playlist',
            description='Sample playlist',
            creator=self.user,
            likes=5,
            spotify_id='sp123',
            spotify_uri='spotify:playlist:sp123'
        )
        self.assertEqual(str(playlist), 'Test Playlist')
        self.assertEqual(playlist.likes, 5)
        self.assertEqual(playlist.creator.username, 'tester')

    def test_song_creation_and_relation(self):
        playlist = Playlist.objects.create(name='Test', creator=self.user, spotify_id='xyz1')
        song = Song.objects.create(playlist=playlist, name='Track 1', artist='Artist X')
        self.assertEqual(str(song), 'Track 1')
        self.assertEqual(song.playlist, playlist)
        self.assertEqual(playlist.sample_songs.count(), 1)


class HomeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='alice', password='1234')
        Playlist.objects.create(name='Playlist A', creator=self.user, likes=10, spotify_id='pA')
        Playlist.objects.create(name='Playlist B', creator=self.user, likes=2, spotify_id='pB')



    def test_home_view_renders(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home/index.html')
        self.assertContains(response, 'Playlist A')

    @patch('home.views.SpotifyAPIHelper.fetch_playlists', return_value=[])
    def test_home_view_no_spotify_call_when_playlists_exist(self, mock_fetch):
        self.client.get(reverse('home'))
        mock_fetch.assert_not_called()

    @patch('home.views.SpotifyAPIHelper.fetch_playlists')
    @patch('home.views.SpotifyAPIHelper.import_playlist')
    def test_home_view_fetches_from_spotify_when_empty(self, mock_import, mock_fetch):
        Playlist.objects.all().delete()
        mock_fetch.return_value = [{'id': 'sp1', 'name': 'From Spotify', 'tracks': {'href': 'http://tracks'}}]
        mock_import.return_value = Playlist.objects.create(
            name='From Spotify', creator=self.user, spotify_id='sp1'
        )
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'From Spotify')


class SearchViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='bob', password='123')
        self.p1 = Playlist.objects.create(name='Rock Mix', creator=self.user, likes=4, spotify_id='rockmix')
        Song.objects.create(playlist=self.p1, name='Thunderstruck')

    def test_search_by_name(self):
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_by_song_name(self):
        response = self.client.get(reverse('search') + '?q=Thunderstruck')
        self.assertContains(response, 'Rock Mix')

    @patch('home.views.SpotifyAPIHelper.fetch_playlists', return_value=[])
    def test_search_no_results_returns_message(self, mock_fetch):
        response = self.client.get(reverse('search') + '?q=Nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No playlists found')

    @patch('home.views.SpotifyAPIHelper.fetch_playlists')
    @patch('home.views.SpotifyAPIHelper.import_playlist')
    def test_search_fetches_from_spotify_when_not_found(self, mock_import, mock_fetch):
        Playlist.objects.all().delete()
        mock_fetch.return_value = [{'id': 'abc', 'name': 'Imported', 'tracks': {'href': 'link'}}]
        mock_import.return_value = Playlist.objects.create(
            name='Imported', creator=self.user, spotify_id='abc')
        response = self.client.get(reverse('search') + '?q=something')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Imported')


class ProfileViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='charlie', password='test')
        Playlist.objects.create(name='User Playlist', creator=self.user, spotify_id='user1')

    def test_profile_page_valid_user(self):
        response = self.client.get(reverse('profile', args=[self.user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "charlie's Playlists")

    def test_profile_page_invalid_user(self):
        response = self.client.get(reverse('profile', args=[999]))
        self.assertEqual(response.status_code, 404)


class LogoutViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='pass')

    def test_logout_clears_session(self):
        session = self.client.session
        session['key'] = 'value'
        session.save()
        response = self.client.get(reverse('logout'))
        self.assertNotIn('key', self.client.session)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('home'))


class SpotifyAPIHelperTests(TestCase):
    @patch('home.views.requests.post')
    def test_get_access_token_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'abc123'}
        token = SpotifyAPIHelper.get_access_token()
        self.assertEqual(token, 'abc123')

    @patch('home.views.requests.post')
    def test_get_access_token_failure(self, mock_post):
        mock_post.return_value.status_code = 400
        with self.assertRaises(Exception):
            SpotifyAPIHelper.get_access_token()

    @patch('home.views.requests.get')
    @patch('home.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_success(self, mock_token, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'playlists': {'items': [{'id': 'p1', 'name': 'Playlist'}]}
        }
        data = SpotifyAPIHelper.fetch_playlists('query')
        self.assertEqual(data[0]['id'], 'p1')

    @patch('home.views.requests.get')
    @patch('home.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_error(self, mock_token, mock_get):
        mock_get.return_value.status_code = 400
        data = SpotifyAPIHelper.fetch_playlists('query')
        self.assertEqual(data, [])
