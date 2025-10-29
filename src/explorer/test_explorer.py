from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from unittest.mock import patch, Mock
from explorer.models import Playlist, Song
from explorer.views import SpotifyAPIHelper


class PlaylistModelTests(TestCase):
    """Tests for Playlist model"""

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='testpass')

    def test_create_playlist(self):
        """Test creating a playlist with all fields"""
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
        self.assertEqual(playlist.spotify_id, 'sp123')

    def test_playlist_ordering(self):
        """Test that playlists are ordered by likes descending"""
        p1 = Playlist.objects.create(name='Low', creator=self.user, likes=1, spotify_id='low')
        p2 = Playlist.objects.create(name='High', creator=self.user, likes=10, spotify_id='high')
        playlists = Playlist.objects.all()
        self.assertEqual(playlists[0], p2)
        self.assertEqual(playlists[1], p1)

    def test_song_creation_and_relation(self):
        """Test creating songs and their relationship with playlists"""
        playlist = Playlist.objects.create(name='Test', creator=self.user, spotify_id='xyz1')
        song = Song.objects.create(playlist=playlist, name='Track 1', artist='Artist X')
        self.assertEqual(str(song), 'Track 1')
        self.assertEqual(song.playlist, playlist)
        self.assertEqual(playlist.sample_songs.count(), 1)

    def test_multiple_songs_in_playlist(self):
        """Test adding multiple songs to a playlist"""
        playlist = Playlist.objects.create(name='Multi', creator=self.user, spotify_id='multi')
        Song.objects.create(playlist=playlist, name='Song 1', artist='Artist 1')
        Song.objects.create(playlist=playlist, name='Song 2', artist='Artist 2')
        Song.objects.create(playlist=playlist, name='Song 3', artist='Artist 3')
        self.assertEqual(playlist.sample_songs.count(), 3)


class SearchViewTests(TestCase):
    """Tests for the search functionality"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='bob', password='123')
        self.p1 = Playlist.objects.create(
            name='Rock Mix',
            description='Best rock songs',
            creator=self.user,
            likes=4,
            spotify_id='rockmix'
        )
        Song.objects.create(playlist=self.p1, name='Thunderstruck', artist='AC/DC')

    def test_search_view_renders(self):
        """Test that search view renders successfully"""
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'explorer/search.html')

    def test_search_by_name(self):
        """Test searching for playlists by name"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_by_description(self):
        """Test searching for playlists by description"""
        response = self.client.get(reverse('search') + '?q=Best')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_by_song_name(self):
        """Test searching for playlists by song name"""
        response = self.client.get(reverse('search') + '?q=Thunderstruck')
        self.assertContains(response, 'Rock Mix')

    def test_search_by_creator_username(self):
        """Test searching for playlists by creator username"""
        response = self.client.get(reverse('search') + '?q=bob')
        self.assertContains(response, 'Rock Mix')

    def test_search_case_insensitive(self):
        """Test that search is case insensitive"""
        response = self.client.get(reverse('search') + '?q=ROCK')
        self.assertContains(response, 'Rock Mix')

    @patch('explorer.views.SpotifyAPIHelper.fetch_playlists', return_value=[])
    def test_search_no_results_shows_empty_message(self, mock_fetch):
        """Test that search shows appropriate message when no results found"""
        response = self.client.get(reverse('search') + '?q=Nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No playlists found')

    @patch('explorer.views.SpotifyAPIHelper.fetch_playlists')
    @patch('explorer.views.SpotifyAPIHelper.import_playlist')
    def test_search_fetches_from_spotify_when_not_found_locally(self, mock_import, mock_fetch):
        """Test that search fetches from Spotify API when no local results"""
        Playlist.objects.all().delete()
        mock_fetch.return_value = [
            {
                'id': 'abc',
                'name': 'Imported',
                'description': 'From Spotify',
                'images': [{'url': 'http://image.url'}],
                'followers': {'total': 100},
                'tracks': {'href': 'http://tracks'},
                'uri': 'spotify:playlist:abc'
            }
        ]
        mock_import.return_value = Playlist.objects.create(
            name='Imported',
            creator=self.user,
            spotify_id='abc'
        )
        response = self.client.get(reverse('search') + '?q=something')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Imported')

    def test_search_without_query(self):
        """Test search without query parameter returns all playlists"""
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rock Mix')

    def test_search_results_count_in_context(self):
        """Test that search results count is in context"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertIn('results_count', response.context)
        self.assertEqual(response.context['results_count'], 1)

    def test_search_displays_playlist_card_structure(self):
        """Test that search results use the new playlist card structure"""
        response = self.client.get(reverse('search') + '?q=rock')
        self.assertEqual(response.status_code, 200)
        # Check for new playlist card structure elements
        self.assertContains(response, 'playlist-card')
        self.assertContains(response, 'playlist-image')
        self.assertContains(response, 'playlist-info-compact')


class ProfileViewTests(TestCase):
    """Tests for user profile view"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='charlie', password='test')
        Playlist.objects.create(name='User Playlist 1', creator=self.user, spotify_id='user1')
        Playlist.objects.create(name='User Playlist 2', creator=self.user, spotify_id='user2')

    def test_profile_page_valid_user(self):
        """Test that profile page displays for valid user"""
        response = self.client.get(reverse('profile', args=[self.user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'explorer/profile.html')
        self.assertContains(response, "charlie's Playlists")

    def test_profile_shows_user_playlists(self):
        """Test that profile shows all user's playlists"""
        response = self.client.get(reverse('profile', args=[self.user.id]))
        self.assertContains(response, 'User Playlist 1')
        self.assertContains(response, 'User Playlist 2')

    def test_profile_page_invalid_user(self):
        """Test that profile page returns 404 for non-existent user"""
        response = self.client.get(reverse('profile', args=[999]))
        self.assertEqual(response.status_code, 404)

    def test_profile_shows_only_user_playlists(self):
        """Test that profile only shows playlists created by that user"""
        other_user = User.objects.create_user(username='other', password='pass')
        Playlist.objects.create(name='Other Playlist', creator=other_user, spotify_id='other')

        response = self.client.get(reverse('profile', args=[self.user.id]))
        self.assertContains(response, 'User Playlist 1')
        self.assertNotContains(response, 'Other Playlist')

    def test_profile_displays_playlist_card_structure(self):
        """Test that profile page uses the new playlist card structure"""
        response = self.client.get(reverse('profile', args=[self.user.id]))
        self.assertEqual(response.status_code, 200)
        # Check for new playlist card structure elements
        self.assertContains(response, 'playlist-card')
        self.assertContains(response, 'playlist-image')
        self.assertContains(response, 'playlist-info-compact')


class LogoutViewTests(TestCase):
    """Tests for logout functionality"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='tester', password='pass')

    def test_logout_clears_session(self):
        """Test that logout clears all session data"""
        session = self.client.session
        session['key'] = 'value'
        session['spotify_token'] = 'token123'
        session.save()

        response = self.client.get(reverse('logout'))

        self.assertNotIn('key', self.client.session)
        self.assertNotIn('spotify_token', self.client.session)
        self.assertEqual(response.status_code, 302)

    def test_logout_redirects_to_home(self):
        """Test that logout redirects to home page"""
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('home'))


class SpotifyAPIHelperTests(TestCase):
    """Tests for Spotify API helper functions"""

    @patch('explorer.views.requests.post')
    def test_get_access_token_success(self, mock_post):
        """Test successful token retrieval"""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'abc123'}

        token = SpotifyAPIHelper.get_access_token()

        self.assertEqual(token, 'abc123')
        mock_post.assert_called_once()

    @patch('explorer.views.requests.post')
    def test_get_access_token_failure(self, mock_post):
        """Test token retrieval failure raises exception"""
        mock_post.return_value.status_code = 400

        with self.assertRaises(Exception) as context:
            SpotifyAPIHelper.get_access_token()

        self.assertIn('Failed to get Spotify access token', str(context.exception))

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_success(self, mock_token, mock_get):
        """Test successful playlist fetching from Spotify"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'playlists': {'items': [
                {'id': 'p1', 'name': 'Playlist 1'},
                {'id': 'p2', 'name': 'Playlist 2'}
            ]}
        }

        data = SpotifyAPIHelper.fetch_playlists('query')

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['id'], 'p1')
        self.assertEqual(data[1]['id'], 'p2')

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_error(self, mock_token, mock_get):
        """Test playlist fetching handles errors gracefully"""
        mock_get.return_value.status_code = 400

        data = SpotifyAPIHelper.fetch_playlists('query')

        self.assertEqual(data, [])

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='tok')
    def test_fetch_playlists_with_limit(self, mock_token, mock_get):
        """Test fetch playlists respects limit parameter"""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'playlists': {'items': [{'id': f'p{i}', 'name': f'Playlist {i}'} for i in range(5)]}
        }

        SpotifyAPIHelper.fetch_playlists('query', limit=5)

        # Check that the API was called with the correct limit
        call_args = mock_get.call_args
        self.assertEqual(call_args[1]['params']['limit'], 5)

    @patch('explorer.views.User.objects.get_or_create')
    @patch('explorer.views.Playlist.objects.get_or_create')
    def test_import_playlist(self, mock_playlist_create, mock_user_create):
        """Test importing a playlist from Spotify data"""
        mock_user = Mock()
        mock_user.username = 'spotify_user'
        mock_user_create.return_value = (mock_user, True)

        mock_playlist = Mock()
        mock_playlist.name = 'Imported Playlist'
        mock_playlist_create.return_value = (mock_playlist, True)

        playlist_data = {
            'id': 'sp123',
            'name': 'Imported Playlist',
            'description': 'A test playlist',
            'images': [{'url': 'http://image.url'}],
            'followers': {'total': 100},
            'uri': 'spotify:playlist:sp123',
            'tracks': {'href': 'http://tracks'}
        }

        result = SpotifyAPIHelper.import_playlist(playlist_data)

        self.assertIsNotNone(result)
        mock_playlist_create.assert_called_once()

    @patch('explorer.views.requests.get')
    @patch('explorer.views.SpotifyAPIHelper.get_access_token', return_value='token')
    def test_fetch_and_add_songs(self, mock_token, mock_get):
        """Test fetching and adding songs to a playlist"""
        user = User.objects.create_user(username='test', password='pass')
        playlist = Playlist.objects.create(
            name='Test',
            creator=user,
            spotify_id='test123'
        )

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'items': [
                {
                    'track': {
                        'id': 't1',
                        'name': 'Song 1',
                        'artists': [{'name': 'Artist 1'}]
                    }
                },
                {
                    'track': {
                        'id': 't2',
                        'name': 'Song 2',
                        'artists': [{'name': 'Artist 2'}, {'name': 'Artist 3'}]
                    }
                }
            ]
        }

        SpotifyAPIHelper.fetch_and_add_songs(playlist, 'http://tracks', limit=2)

        self.assertEqual(playlist.sample_songs.count(), 2)
        songs = playlist.sample_songs.all()
        self.assertEqual(songs[0].name, 'Song 1')
        self.assertEqual(songs[1].artist, 'Artist 2, Artist 3')


class PlaylistCardTemplateTests(TestCase):
    """Tests for the new playlist card template structure"""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.playlist = Playlist.objects.create(
            name='Test Playlist',
            description='Test Description',
            creator=self.user,
            likes=10,
            spotify_id='test123',
            spotify_uri='spotify:playlist:test123',
            cover_image='http://example.com/image.jpg'
        )
        Song.objects.create(playlist=self.playlist, name='Test Song 1', artist='Test Artist 1')
        Song.objects.create(playlist=self.playlist, name='Test Song 2', artist='Test Artist 2')

    def test_playlist_card_has_correct_structure(self):
        """Test that playlist card has the new structure with all elements"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertEqual(response.status_code, 200)

        # Check for main structure elements
        self.assertContains(response, 'class="playlist-card"')
        self.assertContains(response, 'class="playlist-image"')
        self.assertContains(response, 'class="playlist-info-compact"')
        self.assertContains(response, 'class="playlist-title"')

    def test_playlist_card_displays_cover_image(self):
        """Test that playlist card displays cover image when available"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'http://example.com/image.jpg')
        self.assertContains(response, 'alt="Test Playlist"')

    def test_playlist_card_displays_placeholder_when_no_image(self):
        """Test that playlist card displays emoji placeholder when no cover image"""
        self.playlist.cover_image = ''
        self.playlist.save()

        response = self.client.get(reverse('search') + '?q=Test')
        # The template shows a music emoji 🎵 when there's no image
        self.assertContains(response, '🎵')

    def test_playlist_card_displays_creator_username(self):
        """Test that playlist card displays creator username"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'testuser')
        self.assertContains(response, 'creator-name')

    def test_playlist_card_displays_likes(self):
        """Test that playlist card displays likes count"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'likes-count')
        self.assertContains(response, '10')
        self.assertContains(response, '👍')

    def test_playlist_card_shows_spotify_link_when_available(self):
        """Test that playlist card shows Spotify link when spotify_uri is set"""
        response = self.client.get(reverse('search') + '?q=Test')
        self.assertContains(response, 'View on Spotify')
        self.assertContains(response, f'https://open.spotify.com/playlist/{self.playlist.spotify_id}')

    def test_playlist_card_hides_spotify_link_when_not_available(self):
        """Test that playlist card hides Spotify link when spotify_uri is not set"""
        self.playlist.spotify_uri = ''
        self.playlist.save()

        response = self.client.get(reverse('search') + '?q=Test')
        self.assertNotContains(response, 'View on Spotify')
